"""QGIS Processing script: make CoFrance text points from placed FIX symbols.

Install using Processing Toolbox > Scripts > Add Script to Toolbox.
Requires QGIS 3.40+. No additional Python packages are needed.
"""

from contextlib import closing
import gc
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from osgeo import gdal
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QComboBox, QCompleter
from processing.gui.wrappers import MapLayerWidgetWrapper

from qgis.core import (
    NULL, Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFeature, QgsGeometry, QgsPointXY, QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingContext, QgsProcessingException, QgsProcessingMultiStepFeedback,
    QgsProcessingOutputMultipleLayers, QgsProcessingOutputNumber,
    QgsProcessingParameterCrs, QgsProcessingParameterEnum, QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber, QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer, QgsRectangle, QgsSpatialIndex,
    QgsVectorLayer, QgsWkbTypes,
)


# Absolute so QGIS can copy this script into the user's Processing profile.
# Update this one path if the repository is moved.
TEXT_TEMPLATE = Path(
    r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps"
    r"\QGIS\templates\cofrance_text.gpkg"
)
QGIS_DIRECTORY = TEXT_TEMPLATE.parent.parent
MASTER_GROUPS = {
    "Navaids": (("LFXX_NDB", "ident"), ("LFXX_VOR", "ident")),
    "VOR": (("LFXX_VOR", "ident"),),
    "NDB": (("LFXX_NDB", "ident"),),
    "FIX": (("LFXX_FIX_ENR", "ident"), ("LFXX_FIX_PROC", "ident")),
    "ARP": (("LFXX_Airports", "icao"),),
}


def normalized_name(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def read_only_layer(source, name):
    options = QgsVectorLayer.LayerOptions()
    options.forceReadOnly = True
    options.loadDefaultStyle = False
    return QgsVectorLayer(source, name, "ogr", options)


def rename_text_layer(package, original, name):
    """Rename through the GeoPackage driver so spatial metadata stays valid."""
    if original == name:
        return name
    old_sql = '"' + original.replace('"', '""') + '"'
    new_sql = '"' + name.replace('"', '""') + '"'
    dataset = None
    try:
        with gdal.ExceptionMgr():
            dataset = gdal.OpenEx(str(package), gdal.OF_VECTOR | gdal.OF_UPDATE)
            result = dataset.ExecuteSQL("ALTER TABLE " + old_sql + " RENAME TO " + new_sql)
            if result is not None:
                dataset.ReleaseResultSet(result)
    except RuntimeError as error:
        raise QgsProcessingException("Cannot name the output layer '{}': {}".format(name, error)) from error
    finally:
        dataset = None
    # layer_styles is QGIS-specific and is not renamed by GDAL.
    with closing(sqlite3.connect(package)) as db:
        db.execute("UPDATE gpkg_contents SET identifier=? WHERE table_name=?", (name, name))
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='layer_styles'").fetchone():
            db.execute("UPDATE layer_styles SET f_table_name=? WHERE f_table_name=?", (name, original))
        db.commit()
    return name


def detect_master_group(reference):
    # The project layer name takes priority; generic layer names fall back to
    # the GeoPackage filename (e.g. a table displayed as 'cofrance_symbols').
    for value in (reference.name(), Path(reference.source().split("|")[0]).stem):
        value = value.upper()
        if "NAVAID" in value:
            return "Navaids"
        if re.search(r"(?:^|[^A-Z0-9])VOR(?:$|[^A-Z0-9])", value):
            return "VOR"
        if re.search(r"(?:^|[^A-Z0-9])NDB(?:$|[^A-Z0-9])", value):
            return "NDB"
        if re.search(r"(?:^|[^A-Z0-9])ARPS?(?:$|[^A-Z0-9])", value):
            return "ARP"
        if "FIX" in value:
            return "FIX"
    raise QgsProcessingException(
        "Cannot choose masters for '{}'. Use a placed-symbol layer whose name "
        "contains Navaids, VOR, NDB, FIX or ARP (ARPs is also accepted).".format(reference.name()))


def load_master_layers(group, context, feedback):
    project = context.project()
    loaded = list(project.mapLayers().values()) if project is not None else []
    masters = []
    for stem, field in MASTER_GROUPS[group]:
        tag = normalized_name(stem)
        candidates = [layer for layer in loaded if isinstance(layer, QgsVectorLayer)
                      and layer.isValid() and (
                          normalized_name(layer.name()) == tag
                          or normalized_name(Path(layer.source().split("|")[0]).stem) == tag)]
        candidates.sort(key=lambda layer: normalized_name(layer.name()) != tag)
        layer = candidates[0] if candidates else None
        if layer is None:
            paths = [QGIS_DIRECTORY / "navdata" / (stem + ".gpkg"),
                     QGIS_DIRECTORY / "misc" / (stem + ".gpkg")]
            if group == "ARP":
                # Airport source used by the existing CoFrance project.
                paths.append(QGIS_DIRECTORY.parent.parent / "QGIS-Diagrams" / "QGIS"
                             / "src" / "misc" / (stem + ".gpkg"))
            path = next((path for path in paths if path.is_file()), None)
            if path is not None:
                layer = read_only_layer(str(path), stem)
        if layer is None or not layer.isValid():
            raise QgsProcessingException(
                "Required master '{}' is unavailable. Load it in QGIS with that "
                "name, or put {}.gpkg in QGIS/navdata.".format(stem, stem))
        if (not layer.crs().isValid() or layer.geometryType() != QgsWkbTypes.PointGeometry
                or layer.fields().indexFromName(field) < 0):
            raise QgsProcessingException(
                "Master '{}' must be a point layer with a defined CRS and a '{}' field.".format(
                    stem, field))
        feedback.pushInfo("Using master '{}' (name field: {}).".format(layer.name(), field))
        masters.append((layer, field))
    return masters


class SearchableLayerWidgetWrapper(MapLayerWidgetWrapper):
    """Keep the native layer/file picker and add substring completion."""

    def createWidget(self):
        widget = super().createWidget()
        # Batch and model dialogs retain their normal QGIS controls.
        if hasattr(self, "combo") and hasattr(self.combo, "setLayer"):
            self.combo.setEditable(True)
            for combo in self.combo.findChildren(QComboBox):
                completer = combo.completer()
                if completer is not None:
                    completer.setCaseSensitivity(Qt.CaseInsensitive)
                    completer.setFilterMode(Qt.MatchContains)
                    completer.setCompletionMode(QCompleter.PopupCompletion)
                if combo.lineEdit() is not None:
                    combo.lineEdit().setPlaceholderText("Type part of a layer name...")
                    combo.lineEdit().setToolTip(
                        "Type any part of the layer name, then choose a matching layer.")
        return widget


def point_parts(geometry):
    """Return actual point parts, never centroids of a multipoint."""
    if geometry.isNull() or geometry.isEmpty():
        return []
    if geometry.type() != QgsWkbTypes.PointGeometry:
        raise QgsProcessingException("Inputs must contain point geometries.")
    return geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]


def geopackage_tables(path):
    """Inspect all spatial tables, including packages containing several layers."""
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        tables = db.execute("SELECT table_name, geometry_type_name FROM gpkg_geometry_columns").fetchall()
        result = []
        for table, geometry_type in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            fields = {row[1] for row in db.execute("PRAGMA table_info(" + quoted + ")")}
            result.append((table, geometry_type.upper(), fields))
        return result


def regional_location(path, root, group):
    """Locate the map root and base name from its 0_ or 4_ source folder."""
    for parent in (path.parent, *path.parent.parents):
        match = re.fullmatch(r"([04])_(CCA_.+)", parent.name, re.IGNORECASE)
        if match:
            number, base = match.groups()
            if group == "ARP" and number != "4":
                return None
            if group != "ARP" and number != "0":
                return None
            if number == "4":
                base = re.sub(r"_VFR$", "", base, flags=re.IGNORECASE)
            return parent.parent, base, parent
        if parent == root:
            break
    # Also support a flat folder containing the symbol GeoPackages.
    base = re.sub(r"[^\w-]+", "_", root.name).strip("_")
    if not base.upper().startswith("CCA_"):
        base = "CCA_" + base
    return root, base, path.parent


def existing_name_layer(folder, group):
    """Any text layer in a 3_ names folder counts; VFR needs ARP-specific names."""
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.gpkg")):
        arp_filename = re.search(r"arps?names", normalized_name(path.stem))
        if group == "ARP" and arp_filename:
            return path
        for table, geometry_type, fields in geopackage_tables(path):
            if {"uuid", "text"} <= fields and (group != "ARP" or
                    re.search(r"arps?names", normalized_name(table))):
                return path
    return None


def plan_folder_outputs(root, feedback):
    """Build the complete batch before creating folders or writing outputs."""
    groups = {}
    for path in sorted(root.rglob("*.gpkg")):
        if feedback.isCanceled():
            raise QgsProcessingException("Canceled during folder scan.")
        try:
            tables = geopackage_tables(path)
        except sqlite3.Error as error:
            feedback.pushWarning("Cannot inspect '{}': {}".format(path, error))
            continue
        for table, geometry_type, fields in tables:
            if geometry_type not in ("POINT", "MULTIPOINT") or "symbol_type" not in fields:
                continue
            source = str(path) + "|layername=" + table
            reference = read_only_layer(source, path.stem + " (" + table + ")")
            try:
                group = detect_master_group(reference)
            except QgsProcessingException:
                continue
            location = regional_location(path, root, group)
            if location is None:
                continue
            map_root, base, source_folder = location
            # ARP outputs belong beside their symbols; FIX/Navaids combine all
            # symbol tables for the same regional map into one output.
            key = (map_root, base, group, source_folder if group == "ARP" else None)
            groups.setdefault(key, []).append((source, path.stem + " (" + table + ")"))

    jobs = []
    existing = 0
    for (map_root, base, group, source_folder), sources in groups.items():
        if group == "ARP":
            folders = [source_folder]
        else:
            tag = normalized_name(base)
            folders = []
            for folder in sorted(map_root.iterdir()):
                if not folder.is_dir() or not folder.name.startswith("3_"):
                    continue
                normalized = normalized_name(folder.name)
                if tag not in normalized or "names" not in normalized:
                    continue
                is_navaid = "navaid" in normalized
                if ((group == "Navaids" and is_navaid)
                        or (group in ("VOR", "NDB") and (group.lower() in normalized or is_navaid))
                        or (group == "FIX" and "fix" in normalized and not is_navaid)):
                    folders.append(folder)
            if not folders:
                folders = [map_root / ("3_" + base + "_" + group + "_Names")]
        found = next((path for folder in folders
                      if (path := existing_name_layer(folder, group)) is not None), None)
        if found is not None:
            existing += 1
            feedback.pushInfo("Already present; skipped {} names for {}: {}".format(group, base, found))
            continue
        output = folders[0] / (base + "_" + group + "_Names.gpkg")
        if output.exists():
            existing += 1
            feedback.pushWarning("Output path already exists; left unchanged: " + str(output))
            continue
        jobs.append((group, base, sources, output))
    return jobs, existing


def combine_symbol_layers(sources, name, context, feedback):
    """Merge disk layers without losing activation fields or individual points."""
    layers = [read_only_layer(source, label) for source, label in sources]
    if any(not layer.isValid() or not layer.crs().isValid() for layer in layers):
        raise QgsProcessingException("A source symbol layer is invalid or has no CRS: " + name)
    combined = QgsVectorLayer("MultiPoint", name, "memory")
    combined.setCrs(layers[0].crs())
    fields = {}
    for layer in layers:
        for field in layer.fields():
            if field.name() != "fid":
                fields.setdefault(field.name(), field)
    combined.dataProvider().addAttributes(list(fields.values()))
    combined.updateFields()
    for layer in layers:
        transform = QgsCoordinateTransform(layer.crs(), combined.crs(), context.transformContext())
        for source in layer.getFeatures():
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled while reading symbol layers.")
            if source.geometry().isEmpty():
                continue
            geometry = QgsGeometry(source.geometry())
            geometry.transform(transform)
            geometry.convertToMultiType()
            feature = QgsFeature(combined.fields())
            feature.setGeometry(geometry)
            attributes = source.attributeMap()
            for field in combined.fields():
                if field.name() in attributes:
                    feature[field.name()] = attributes[field.name()]
            ok, _ = combined.dataProvider().addFeatures([feature])
            if not ok:
                raise QgsProcessingException("Cannot combine symbol features for " + name)
    return combined


class GenerateFixNames(QgsProcessingAlgorithm):
    def name(self):
        return "generate_fix_names"

    def displayName(self):
        return "Generate CoFrance point names"

    def group(self):
        return "CoFrance"

    def groupId(self):
        return "cofrance"

    def createInstance(self):
        return GenerateFixNames()

    def shortHelpString(self):
        return (
            "Choose Manual for one placed-symbol layer and output GeoPackage, or Automatic for a folder and its subfolders. "
            "Fill only the inputs for the chosen mode. Expand Advanced for point-type overrides, offsets, matching and category overrides. "
            "Automatic mode recursively scans a regional folder for symbol GeoPackages, "
            "combines FIX/Navaids per map, and creates only missing names layers in "
            "3_..._Names folders; ARP names go beside their symbols in 4_..._VFR. "
            "Existing text layers, including empty ones, are left unchanged. "
            "Choose masters automatically from the placed-symbol layer name: "
            "Navaids = LFXX_NDB + LFXX_VOR; FIX = LFXX_FIX_ENR + LFXX_FIX_PROC; "
            "ARP/ARPs = LFXX_Airports (icao field). "
            "Match placed symbol coordinates to the combined masters and create "
            "one text point per matched master point. Matching uses every feature "
            "in each layer (respects layer filters, ignores selections). Unmatched "
            "or ambiguous points are reported and skipped; a distant nearest FIX "
            "is never substituted. Repeated placements of the same master point "
            "are deduplicated. Geometry comes from the master and is shifted in "
            "the working CRS before conversion to the template CRS. "
            "Defaults reproduce Lille: EPSG:2154, X=0, Y=3500 metres. "
            "Text categories follow the source: FIX_NAME, VOR_NAME, NDB_NAME or ARP_NAME. "
            "The optional manual category overrides this. The bundled cofrance_text.gpkg template is selected automatically. "
            "Its schema and stored style are copied to a NEW file; existing files "
            "are never overwritten. Optional activation and zoom attributes are "
            "copied from the placed symbol, using the first occurrence if repeated. "
            "The output is added to the current project after running."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum(
            "MODE", "Mode", options=["Manual - one layer", "Automatic - folder and subfolders"],
            defaultValue=0))
        self.addParameter(QgsProcessingParameterFile(
            "ROOT_FOLDER", "Automatic: regional folder to scan",
            behavior=QgsProcessingParameterFile.Folder, optional=True))
        reference = QgsProcessingParameterVectorLayer(
            "REFERENCE", "Manual: placed symbols (Navaids / FIX / ARP)",
            [QgsProcessing.TypeVectorPoint], optional=True)
        reference.setMetadata({"widget_wrapper": {"class": SearchableLayerWidgetWrapper}})
        self.addParameter(reference)
        self.addParameter(QgsProcessingParameterEnum(
            "MASTER_TYPE", "Manual: point type (automatic uses layer/source name)",
            options=["Automatic", "FIX", "Navaids", "VOR", "NDB", "ARP"], defaultValue=0))
        self.addParameter(QgsProcessingParameterString(
            "CATEGORY", "Manual: category override (empty = FIX_NAME / VOR_NAME / NDB_NAME / ARP_NAME)", defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterCrs(
            "WORK_CRS", "Working CRS for matching and offsets (must use metres)",
            defaultValue="EPSG:2154"))
        self.addParameter(QgsProcessingParameterNumber(
            "TOLERANCE", "Maximum matching distance (metres)",
            type=QgsProcessingParameterNumber.Double, defaultValue=5, minValue=0))
        for key, description, value in (
            ("DX", "X offset (metres; positive = east in working CRS)", 0),
            ("DY", "Y offset (metres; positive = north in working CRS)", 3500),
        ):
            self.addParameter(QgsProcessingParameterNumber(
                key, description, type=QgsProcessingParameterNumber.Double, defaultValue=value))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT", "Manual: new text GeoPackage", "GeoPackage (*.gpkg)", optional=True))
        # One tool covers both workflows; defaults keep the main form short.
        for key in ("MASTER_TYPE", "CATEGORY", "WORK_CRS", "TOLERANCE", "DX", "DY"):
            parameter = self.parameterDefinition(key)
            parameter.setFlags(parameter.flags() | parameter.FlagAdvanced)
        self.parameterDefinition("REFERENCE").setDescription("Manual: placed symbols")
        self.parameterDefinition("MASTER_TYPE").setDescription("Manual: point type (Automatic detects the layer name)")
        self.parameterDefinition("OUTPUT").setDescription("Manual: new names GeoPackage")
        self.addOutput(QgsProcessingOutputMultipleLayers("OUTPUTS", "Created text layers"))
        for key, description in (("LABEL_COUNT", "Labels created"),
                                 ("SKIPPED_COUNT", "Unmatched or ambiguous points"),
                                 ("DUPLICATE_COUNT", "Repeated placements"),
                                 ("FILE_COUNT", "Name GeoPackages created"),
                                 ("EXISTING_COUNT", "Existing names left unchanged"),
                                 ("FAILED_COUNT", "Failed outputs")):
            self.addOutput(QgsProcessingOutputNumber(key, description))

    def processAlgorithm(self, parameters, context, feedback):
        if self.parameterAsEnum(parameters, "MODE", context) == 1:
            return self.process_folder(parameters, context, feedback)
        result = self.generate_layer(parameters, context, feedback)
        result.update(OUTPUTS=[result["OUTPUT"]], FILE_COUNT=1, EXISTING_COUNT=0, FAILED_COUNT=0)
        return result

    def process_folder(self, parameters, context, feedback):
        folder = self.parameterAsFile(parameters, "ROOT_FOLDER", context)
        if not folder or not Path(folder).is_dir():
            raise QgsProcessingException("Automatic mode requires a regional folder to scan.")
        root = Path(folder).resolve()
        jobs, existing = plan_folder_outputs(root, feedback)
        result = {"OUTPUT": "", "OUTPUTS": [], "FILE_COUNT": 0, "EXISTING_COUNT": existing,
                  "FAILED_COUNT": 0, "LABEL_COUNT": 0, "SKIPPED_COUNT": 0, "DUPLICATE_COUNT": 0}
        if not jobs:
            feedback.pushInfo("No missing names layers found." if existing else
                              "No recognised FIX, Navaid or ARP symbol layers found in this folder.")
            return result
        steps = QgsProcessingMultiStepFeedback(len(jobs), feedback)
        for step, (group, base, sources, output) in enumerate(jobs):
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled. Previously completed outputs remain saved.")
            steps.setCurrentStep(step)
            feedback.pushInfo("Creating {} from {} source layer(s): {}".format(
                group, len(sources), output))
            try:
                reference = combine_symbol_layers(sources, base + "_" + group, context, steps)
                output.parent.mkdir(parents=True, exist_ok=True)
                job_parameters = dict(parameters, REFERENCE=reference, OUTPUT=str(output), CATEGORY="", MASTER_TYPE=0)
                generated = self.generate_layer(job_parameters, context, steps)
            except (QgsProcessingException, OSError, sqlite3.Error) as error:
                if feedback.isCanceled():
                    raise QgsProcessingException("Canceled. Previously completed outputs remain saved.")
                result["FAILED_COUNT"] += 1
                feedback.reportError("Could not create '{}': {}".format(output, error), fatalError=False)
                continue
            result["OUTPUTS"].append(generated["OUTPUT"])
            result["FILE_COUNT"] += 1
            for key in ("LABEL_COUNT", "SKIPPED_COUNT", "DUPLICATE_COUNT"):
                result[key] += generated[key]
        feedback.pushInfo("Created {} name GeoPackage(s); {} already present; {} failed.".format(
            result["FILE_COUNT"], existing, result["FAILED_COUNT"]))
        return result

    def generate_layer(self, parameters, context, feedback):
        reference = self.parameterAsVectorLayer(parameters, "REFERENCE", context)
        category = self.parameterAsString(parameters, "CATEGORY", context).strip()
        work_crs = self.parameterAsCrs(parameters, "WORK_CRS", context)
        tolerance = self.parameterAsDouble(parameters, "TOLERANCE", context)
        dx = self.parameterAsDouble(parameters, "DX", context)
        dy = self.parameterAsDouble(parameters, "DY", context)
        template_path = TEXT_TEMPLATE.resolve()
        output_path = Path(self.parameterAsFileOutput(parameters, "OUTPUT", context)).resolve()
        if not reference or not reference.isValid():
            raise QgsProcessingException("Choose a valid placed-symbol point layer.")
        type_index = self.parameterAsEnum(parameters, "MASTER_TYPE", context)
        group = ("FIX", "Navaids", "VOR", "NDB", "ARP")[type_index-1] if type_index else detect_master_group(reference)
        feedback.pushInfo("Detected {} from reference '{}'.".format(group, reference.name()))
        masters = load_master_layers(group, context, feedback)
        reference_is_text = (
            all(reference.fields().indexFromName(field) >= 0 for field in ("uuid", "text"))
            and reference.fields().indexFromName("symbol_type") < 0
        )
        if reference_is_text:
            feedback.pushWarning(
                "Reference '{}' has uuid/text fields and may already be a text layer. "
                "For name generation, select the placed symbol layer. If its text values "
                "are already the required labels, export that existing text layer directly.".format(
                    reference.name()))
        if category.upper() in ("AUTO", "IFR_FIX_NAMES", "IFR_FIX_NAMS"):
            feedback.pushInfo("Legacy/automatic category replaced with per-point name categories.")
            category = ""
        if not work_crs.isValid() or work_crs.mapUnits() != Qgis.DistanceUnit.Meters:
            raise QgsProcessingException("Choose a working CRS in metres, e.g. EPSG:2154.")
        if not reference.crs().isValid():
            raise QgsProcessingException("The reference layer must have a defined CRS.")
        if output_path.suffix.lower() != ".gpkg" or not output_path.parent.is_dir():
            raise QgsProcessingException("Choose an output .gpkg in an existing directory.")
        if output_path.exists():
            raise QgsProcessingException("Output already exists. Choose a new filename.")
        if not template_path.is_file():
            raise QgsProcessingException(
                "The bundled text template does not exist: {}. "
                "Update TEXT_TEMPLATE in the script if the repository was moved.".format(template_path))

        with closing(sqlite3.connect(template_path.as_uri() + "?mode=ro", uri=True)) as db:
            tables = db.execute("SELECT table_name FROM gpkg_geometry_columns").fetchall()
            if len(tables) != 1:
                raise QgsProcessingException("The template must have exactly one spatial table.")
            table = tables[0][0]
            quoted_table = '"' + table.replace('"', '""') + '"'
            if db.execute("SELECT count(*) FROM " + quoted_table).fetchone()[0]:
                raise QgsProcessingException("Use an EMPTY template, not an existing text layer.")

        ref_transform = QgsCoordinateTransform(reference.crs(), work_crs, context.transformContext())
        index = QgsSpatialIndex()
        points = {}
        merged = 0
        for master, name_field in masters:
            tag = normalized_name(master.name()) + normalized_name(Path(master.source().split("|")[0]).stem)
            auto_category = ("ARP_NAME" if group == "ARP" else "VOR_NAME" if group == "VOR" or "lfxxvor" in tag
                             else "NDB_NAME" if group == "NDB" or "lfxxndb" in tag else "FIX_NAME")
            transform = QgsCoordinateTransform(master.crs(), work_crs, context.transformContext())
            for feature in master.getFeatures():
                if feedback.isCanceled():
                    raise QgsProcessingException("Canceled before creating output.")
                name = feature[name_field]
                if name is None or name == NULL or not str(name).strip():
                    continue
                name = str(name).strip()
                for part in point_parts(feature.geometry()):
                    point = transform.transform(QgsPointXY(part))
                    # Shared identifiers within 1 metre represent the same master
                    # point; retain the first source's coordinates. Different
                    # identifiers remain distinct and can still be ambiguous.
                    box = QgsRectangle(point.x() - 1, point.y() - 1,
                                       point.x() + 1, point.y() + 1)
                    if any(points[key][0] == name and points[key][1].distance(point) <= 1
                           for key in index.intersects(box)):
                        merged += 1
                        continue
                    key = len(points)
                    indexed = QgsFeature()
                    indexed.setId(key)
                    indexed.setGeometry(QgsGeometry.fromPointXY(point))
                    index.addFeature(indexed)
                    points[key] = (name, point, auto_category)
        if not points:
            raise QgsProcessingException("The selected masters have no named point geometries.")
        feedback.pushInfo("Indexed {} master points; merged {} overlapping records.".format(
            len(points), merged))

        matches = {}
        skipped = duplicates = empty = 0
        for feature in reference.getFeatures():
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled before creating output.")
            parts = point_parts(feature.geometry())
            if not parts:
                empty += 1
            for part in parts:
                point = ref_transform.transform(QgsPointXY(part))
                # Small epsilon permits exact matching after CRS round trips.
                radius = max(tolerance, 0.000001)
                box = QgsRectangle(point.x() - radius, point.y() - radius,
                                   point.x() + radius, point.y() + radius)
                candidates = [key for key in index.intersects(box)
                              if points[key][1].distance(point) <= radius]
                if len(candidates) != 1:
                    skipped += 1
                    nearest_hint = ""
                    if not candidates and skipped <= 5:
                        nearest = index.nearestNeighbor(point, 1)
                        if nearest:
                            nearest_name, nearest_point, _ = points[nearest[0]]
                            nearest_hint = " Nearest master point: '{}' at {:.1f} m.".format(
                                nearest_name, nearest_point.distance(point))
                    feedback.pushWarning(
                        "Reference feature {}: {} master matches within {} m; skipped.{}".format(
                            feature.id(), len(candidates), tolerance, nearest_hint))
                    continue
                key = candidates[0]
                if key in matches:
                    duplicates += 1
                else:
                    matches[key] = feature.attributeMap()
        if not matches:
            raise QgsProcessingException(
                "No unambiguous matches between reference '{}' and automatic masters '{}' within {} m. "
                "Check the reference layer name, assigned CRS and matching tolerance.{}".format(
                    reference.name(), ", ".join(layer.name() for layer, field in masters), tolerance,
                    " This reference appears to be an existing text layer (uuid/text fields)."
                    if reference_is_text else ""))

        # SQLite backup also handles templates which have an active WAL journal.
        # Work in a staging file so failures never leave a partial output package.
        handle, staging = tempfile.mkstemp(suffix=".gpkg", prefix="fix_names_", dir=output_path.parent)
        os.close(handle)
        layer = None
        try:
            with closing(sqlite3.connect(template_path.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(staging)) as destination:
                    source.backup(destination)
            table = rename_text_layer(staging, table, output_path.stem)
            layer = QgsVectorLayer(staging + "|layername=" + table, output_path.stem, "ogr")
            if (not layer.isValid() or not layer.crs().isValid()
                    or QgsWkbTypes.flatType(layer.wkbType()) not in
                    (QgsWkbTypes.Point, QgsWkbTypes.MultiPoint)
                    or any(layer.fields().indexFromName(field) < 0 for field in ("uuid", "text"))):
                raise QgsProcessingException("Template must be Point/MultiPoint with uuid and text fields and a CRS.")
            to_output = QgsCoordinateTransform(work_crs, layer.crs(), context.transformContext())
            if not layer.startEditing():
                raise QgsProcessingException("Cannot edit the output GeoPackage.")
            for count, (key, attributes) in enumerate(matches.items(), 1):
                if feedback.isCanceled():
                    raise QgsProcessingException("Canceled; output discarded.")
                name, source_point, auto_category = points[key]
                shifted = to_output.transform(QgsPointXY(source_point.x() + dx, source_point.y() + dy))
                geometry = QgsGeometry.fromPointXY(shifted)
                if QgsWkbTypes.isMultiType(layer.wkbType()):
                    geometry.convertToMultiType()
                label = QgsFeature(layer.fields())
                label.setGeometry(geometry)
                for field in layer.fields():
                    if field.name() not in ("fid", "uuid", "text") and field.name() in attributes:
                        label[field.name()] = attributes[field.name()]
                symbol_kind = str(attributes.get("symbol_type") or "").lower()
                glyph_category = ({"dme": "DME_NAME", "tacan": "TACAN_NAME", "vortac": "VORTAC_NAME"}.get(symbol_kind)
                                  or ("NDB_NAME" if symbol_kind.startswith("ndb") else
                                      "VOR_NAME" if symbol_kind.startswith("vor") else
                                      "ARP_NAME" if symbol_kind.startswith("aerodrome") else auto_category))
                label["uuid"] = category or glyph_category
                label["text"] = name
                if not layer.addFeature(label):
                    raise QgsProcessingException("Cannot insert label: " + name)
                feedback.setProgress(count * 100 / len(matches))
            if not layer.commitChanges():
                raise QgsProcessingException("Cannot save labels: " + "; ".join(layer.commitErrors()))
            layer = None
            gc.collect()  # Release OGR's file handle before renaming on Windows.
            # Reserve the destination exclusively; never overwrite an input or existing output.
            with open(output_path, "xb"):
                pass
            try:
                os.replace(staging, output_path)
            except Exception:
                output_path.unlink()
                raise
        finally:
            if layer is not None:
                layer.rollBack()
            layer = None
            gc.collect()
            for suffix in ("", "-wal", "-shm", "-journal"):
                candidate = Path(staging + suffix)
                if candidate.exists():
                    candidate.unlink()

        feedback.pushInfo("Created {} labels; skipped {} unmatched/ambiguous points, {} repeated placements, "
                          "and {} empty reference features.".format(len(matches), skipped, duplicates, empty))
        if context.project() is not None:
            context.addLayerToLoadOnCompletion(
                str(output_path) + "|layername=" + table,
                QgsProcessingContext.LayerDetails(output_path.stem, context.project(), "OUTPUT"))
        return {"OUTPUT": str(output_path), "LABEL_COUNT": len(matches),
                "SKIPPED_COUNT": skipped, "DUPLICATE_COUNT": duplicates}
