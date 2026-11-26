"""QGIS Processing: create CoFrance symbols and separate names from an identifier list."""

from contextlib import closing
import gc
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from osgeo import gdal
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtXml import QDomDocument
from qgis.core import (
    Qgis, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsDistanceArea, QgsExpression, QgsFeature,
    QgsFeatureRequest, QgsField, QgsGeometry, QgsMarkerSymbol, QgsPalLayerSettings,
    QgsPointXY, QgsProcessingAlgorithm, QgsProcessingContext, QgsProcessingException,
    QgsProcessingOutputNumber, QgsProcessingOutputString, QgsProcessingParameterCrs,
    QgsProcessingParameterEnum, QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination, QgsProcessingParameterNumber,
    QgsProcessingParameterString, QgsRendererCategory, QgsSingleSymbolRenderer,
    QgsSvgMarkerSymbolLayer, QgsTextFormat, QgsVectorLayer,
    QgsVectorLayerSimpleLabeling, QgsWkbTypes,
)

QGIS_DIRECTORY = Path(
    r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS"
)
SOURCES = ("LFXX_VOR", "LFXX_NDB", "LFXX_FIX_ENR", "LFXX_FIX_PROC")
SOURCE_GROUPS = (SOURCES, SOURCES[:2], SOURCES[2:], (SOURCES[0],), (SOURCES[1],),
                 (SOURCES[2],), (SOURCES[3],))
QUALIFIERS = {
    "VOR": SOURCES[:1], "NDB": SOURCES[1:2], "FIX": SOURCES[2:],
    "ENR": SOURCES[2:3], "PROC": SOURCES[3:4],
    **{stem: (stem,) for stem in SOURCES},
}
FIX_SYMBOLS = ("triangle_hollow", "triangle_filled", "circle", "square", "diamond", "point", "cross")
NAV_SVG = {"vor": "VOR.svg", "ndb": "NDB.svg", "vor_dme": "VOR_DME.svg",
           "dme": "DME.svg", "tacan": "TACAN.svg", "vortac": "VORTAC.svg"}


def name_category(point):
    """Semantic text category, independent of the map/layer filename."""
    kind = str(point.get("symbol_type") or "").lower()
    master = str(point.get("master") or "").upper()
    # An explicit symbol type takes priority over the master container name.
    if kind.startswith("ndb"):
        return "NDB_NAME"
    if kind in ("dme", "tacan", "vortac"):
        return kind.upper() + "_NAME"
    if kind.startswith("vor"):
        return "VOR_NAME"
    if kind.startswith("aerodrome"):
        return "ARP_NAME"
    if master == "LFXX_NDB":
        return "NDB_NAME"
    if master == "LFXX_VOR":
        return "VOR_NAME"
    if master == "LFXX_AIRPORTS":
        return "ARP_NAME"
    return "FIX_NAME"


def parse_points(text):
    # A # starts a comment. Use @ for optional master feature IDs.
    text = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    entries = []
    for token in re.split(r"[\s,;]+", text.strip()):
        if not token:
            continue
        match = re.fullmatch(r"(?:(\w+):)?([A-Za-z0-9_-]+)(?:@(\d+))?", token)
        if not match:
            raise QgsProcessingException("Invalid point entry: " + token)
        qualifier, ident, fid = match.groups()
        qualifier = qualifier.upper() if qualifier else None
        if qualifier is not None and qualifier not in QUALIFIERS:
            raise QgsProcessingException("Unknown source qualifier in " + token +
                                         ". Use VOR:, NDB:, FIX:, ENR: or PROC:.")
        if fid is not None and (qualifier is None or len(QUALIFIERS[qualifier]) != 1):
            raise QgsProcessingException("A feature ID needs a specific source, e.g. VOR:BLV@123.")
        entries.append({"entry": token, "ident": ident.upper(), "qualifier": qualifier,
                        "fid": int(fid) if fid else None})
    if not entries:
        raise QgsProcessingException("Paste at least one point identifier, or select a text list.")
    return entries


def load_master(stem, context):
    normal = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    project = context.project()
    candidates = {}
    for layer in project.mapLayers().values() if project else []:
        if isinstance(layer, QgsVectorLayer) and layer.isValid() and (
                normal(layer.name()) == normal(stem)
                or normal(Path(layer.source().split("|")[0]).stem) == normal(stem)):
            candidates[layer.source()] = layer
    if len(candidates) > 1:
        raise QgsProcessingException("Multiple different masters match " + stem + "; rename the extra master.")
    if candidates:
        layer = next(iter(candidates.values()))
    else:
        path = QGIS_DIRECTORY / "navdata" / (stem + ".gpkg")
        if not path.is_file():
            return None
        options = QgsVectorLayer.LayerOptions()
        options.forceReadOnly = True
        options.loadDefaultStyle = False
        layer = QgsVectorLayer(str(path), stem, "ogr", options)
    if (not layer.isValid() or not layer.crs().isValid()
            or layer.geometryType() != QgsWkbTypes.PointGeometry
            or layer.fields().indexFromName("ident") < 0):
        raise QgsProcessingException(stem + " must be a point master with a CRS and an ident field.")
    return layer


def resolve_points(entries, group, context, feedback):
    """Resolve exact names; only merge co-located Enroute/Procedure FIX copies."""
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    measure = QgsDistanceArea()
    measure.setSourceCrs(wgs84, context.transformContext())
    measure.setEllipsoid("WGS84")
    wanted = {entry["ident"] for entry in entries}
    required = set()
    for entry in entries:
        required.update(QUALIFIERS[entry["qualifier"]] if entry["qualifier"] else SOURCE_GROUPS[group])
    by_ident = {}
    for stem in SOURCES:
        if stem not in required:
            continue
        if feedback.isCanceled():
            raise QgsProcessingException("Canceled before writing outputs.")
        layer = load_master(stem, context)
        if layer is None:
            feedback.pushWarning("Unavailable master skipped: " + stem)
            continue
        transform = QgsCoordinateTransform(layer.crs(), wgs84, context.transformContext())
        expression = 'upper(trim("ident")) IN (' + ",".join(
            QgsExpression.quotedString(ident) for ident in sorted(wanted)) + ")"
        for feature in layer.getFeatures(QgsFeatureRequest().setFilterExpression(expression)):
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled before writing outputs.")
            geometry = feature.geometry()
            if geometry.isNull() or geometry.isEmpty():
                continue
            parts = geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]
            if len(parts) != 1:
                raise QgsProcessingException(stem + " fid=" + str(feature.id()) + " must contain exactly one point.")
            point = transform.transform(QgsPointXY(parts[0]))
            ident = str(feature["ident"]).strip().upper()
            by_ident.setdefault(ident, []).append(
                {"ident": ident, "master": stem, "fid": feature.id(), "point": point})
    selected, issues, duplicates = [], [], 0
    for entry in entries:
        if feedback.isCanceled():
            raise QgsProcessingException("Canceled before writing outputs.")
        stems = QUALIFIERS[entry["qualifier"]] if entry["qualifier"] else SOURCE_GROUPS[group]
        candidates = [p for p in by_ident.get(entry["ident"], []) if p["master"] in stems
                      and (entry["fid"] is None or p["fid"] == entry["fid"])]
        distinct = []
        for candidate in candidates:
            equivalent = any(
                {candidate["master"], other["master"]} == set(SOURCES[2:])
                and measure.measureLine(candidate["point"], other["point"]) <= 1 for other in distinct)
            if not equivalent:
                distinct.append(candidate)
        if len(distinct) != 1:
            choices = ["{}:{}@{} ({:.6f}, {:.6f})".format(
                p["master"], p["ident"], p["fid"], p["point"].y(), p["point"].x()) for p in distinct]
            message = "not found" if not distinct else "ambiguous: " + "; ".join(choices)
            issues.append({"entry": entry["entry"], "reason": message})
            feedback.pushWarning(entry["entry"] + ": " + message)
            continue
        candidate = distinct[0]
        if any(other["ident"] == candidate["ident"] and (
                (other["master"], other["fid"]) == (candidate["master"], candidate["fid"])
                or ({other["master"], candidate["master"]} == set(SOURCES[2:])
                    and measure.measureLine(other["point"], candidate["point"]) <= 1))
               for other in selected):
            duplicates += 1
        else:
            selected.append(candidate)
    return selected, issues, duplicates


def clone_template(kind, path, name):
    template = QGIS_DIRECTORY / "templates" / ("cofrance_" + kind + ".gpkg")
    if not template.is_file():
        raise QgsProcessingException("Missing template: " + str(template))
    with closing(sqlite3.connect(template.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        table = source.execute("SELECT table_name FROM gpkg_geometry_columns").fetchone()[0]
        quoted = '"' + table.replace('"', '""') + '"'
        if source.execute("SELECT count(*) FROM " + quoted).fetchone()[0]:
            raise QgsProcessingException("The " + kind + " template must be empty.")
        with closing(sqlite3.connect(path)) as destination:
            source.backup(destination)
    dataset = None
    try:
        with gdal.ExceptionMgr():
            dataset = gdal.OpenEx(str(path), gdal.OF_VECTOR | gdal.OF_UPDATE)
            if table != name:
                dataset.ExecuteSQL("ALTER TABLE " + quoted + ' RENAME TO "' + name.replace('"', '""') + '"')
    finally:
        if dataset is not None:
            dataset.Close()
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE gpkg_contents SET identifier=? WHERE table_name=?", (name, name))
        db.execute("UPDATE layer_styles SET f_table_name=? WHERE f_table_name=?", (name, table))
        db.commit()


def configure_style(layer, text_layer, symbol_types, assets):
    if text_layer:
        settings = QgsPalLayerSettings()
        settings.fieldName = "text"
        settings.isExpression = False
        settings.placement = Qgis.LabelPlacement.OverPoint
        settings.quadOffset = QgsPalLayerSettings.QuadrantOver
        settings.xOffset = settings.yOffset = 0
        fmt = QgsTextFormat()
        fmt.setFont(QFont("Arial"))
        fmt.setFamilies(["Arial"])
        fmt.setSize(10)
        fmt.setSizeUnit(Qgis.RenderUnit.Points)
        settings.setFormat(fmt)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
            {"name": "circle", "color": "0,0,0,0", "outline_style": "no", "size": "0"})))
        return
    categories = []
    for kind in sorted(symbol_types):
        if kind in NAV_SVG:
            file = assets / NAV_SVG[kind]
            if not file.is_file():
                raise QgsProcessingException("Missing navaid artwork: " + str(file))
            symbol = QgsMarkerSymbol()
            symbol.changeSymbolLayer(0, QgsSvgMarkerSymbolLayer(str(file)))
            symbol.setSize(6)
        else:
            shape = {"triangle_hollow": "triangle", "triangle_filled": "triangle"}.get(kind, kind)
            fill = "0,0,0,0" if kind in ("triangle_hollow", "circle", "square", "diamond") else "255,255,255,255"
            symbol = QgsMarkerSymbol.createSimple(
                {"name": "circle" if kind == "point" else shape, "color": fill,
                 "outline_color": "255,255,255,255", "outline_width": "0.25", "size": "4"})
        categories.append(QgsRendererCategory(kind, symbol, kind))
    layer.setRenderer(QgsCategorizedSymbolRenderer("symbol_type", categories))
    layer.setLabelsEnabled(False)


def style_xml(document, stage, output):
    pending = [document.documentElement()]
    while pending:
        element = pending.pop()
        attrs = element.attributes()
        for index in range(attrs.count()):
            attr = attrs.item(index)
            value = attr.nodeValue().replace(str(stage).replace("\\", "/"), str(output).replace("\\", "/"))
            value = value.replace(str(stage), str(output))
            element.setAttribute(attr.nodeName(), value)
        child = element.firstChildElement()
        while not child.isNull():
            pending.append(child)
            child = child.nextSiblingElement()
    return document.toString()


def write_points(kind, name, stage, output, points, fix_symbol, work_crs, dx, dy, context, feedback):
    path = stage / (name + ".gpkg")
    clone_template(kind, path, name)
    layer = None
    text_layer = kind == "text"
    try:
        layer = QgsVectorLayer(str(path) + "|layername=" + name, name, "ogr")
        if not layer.isValid() or not layer.crs().isValid() or not layer.startEditing():
            raise QgsProcessingException("Cannot edit output " + name)
        metadata = [(key, kind) for key, kind in (
            ("navaid_type", QVariant.String), ("type_source", QVariant.String),
            ("type_reference_id", QVariant.String), ("type_distance_m", QVariant.Double),
            ("symbol_component", QVariant.String), ("airport_surface", QVariant.String),
            ("runway_length_m", QVariant.Double)) if any(key in point for point in points)]
        for field in (QgsField("ident", QVariant.String), QgsField("source_master", QVariant.String),
                      QgsField("source_fid", QVariant.LongLong),
                      *(QgsField(key, kind) for key, kind in metadata)):
            if not layer.addAttribute(field):
                raise QgsProcessingException("Cannot add source fields.")
        to_work = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), work_crs, context.transformContext())
        to_output = QgsCoordinateTransform(work_crs, layer.crs(), context.transformContext())
        direct = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), layer.crs(), context.transformContext())
        symbols = set()
        for point in points:
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled; both outputs discarded.")
            anchor = point["point"]
            if text_layer:
                local = to_work.transform(anchor)
                position = to_output.transform(QgsPointXY(local.x() + dx, local.y() + dy))
            else:
                position = direct.transform(anchor)
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromMultiPointXY([position]))
            feature["ident"] = point["ident"]
            feature["source_master"] = point["master"]
            feature["source_fid"] = point["fid"]
            for key, _ in metadata:
                feature[key] = point.get(key)
            if text_layer:
                feature["uuid"] = name_category(point)
                feature["text"] = point["ident"]
            else:
                symbol_type = point.get("symbol_type") or {SOURCES[0]: "vor", SOURCES[1]: "ndb"}.get(point["master"], fix_symbol)
                feature["name"] = name
                feature["symbol_type"] = symbol_type
                symbols.add(symbol_type)
            if not layer.addFeature(feature):
                raise QgsProcessingException("Cannot insert " + point["ident"])
        if not layer.commitChanges():
            raise QgsProcessingException("Cannot save output: " + "; ".join(layer.commitErrors()))
        configure_style(layer, text_layer, symbols, stage / "svg")
        document = QDomDocument()
        error = layer.exportNamedStyle(document)
        if error:
            raise QgsProcessingException(error)
        qml = style_xml(document, stage, output)
    finally:
        if layer is not None:
            if layer.isEditable():
                layer.rollBack()
            sip.delete(layer)
        layer = None
        gc.collect()
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("UPDATE layer_styles SET styleQML=?,useAsDefault=1 WHERE f_table_name=?",
                          (qml, name)).rowcount:
            raise QgsProcessingException("Template has no saved style.")
        db.commit()
    return path.name


class CreatePointsFromList(QgsProcessingAlgorithm):
    def name(self):
        return "create_cofrance_points_from_list"

    def displayName(self):
        return "Create CoFrance points from list"

    def group(self):
        return "CoFrance"

    def groupId(self):
        return "cofrance"

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def createInstance(self):
        return CreatePointsFromList()

    def shortHelpString(self):
        return (
            "Paste point identifiers separated by spaces, commas, semicolons or newlines, or select a text list. "
            "Creates separate symbols and names GeoPackages from the CoFrance templates, plus an import report. "
            "VOR/NDB use their own symbols; FIX uses the chosen FIX symbol. "
            "Prefix entries with VOR:, NDB:, FIX:, ENR: or PROC: to specify a source. "
            "Use a specific source plus @feature_id to resolve duplicates, e.g. VOR:BLV@123. "
            "Names use Arial, Single Labels on text, and Offset from point placement. "
            "Default name coordinates are 3500 metres north in EPSG:2154, matching the existing CoFrance name tool. "
            "Change offsets for your map scale. Missing/ambiguous points stop the run by default; optional skip mode reports them. "
            "Choose a NEW or EMPTY output folder; existing data is never overwritten. Both layers load afterward."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString("POINTS", "Point identifiers (paste list)",
                          defaultValue="", multiLine=True, optional=True))
        self.addParameter(QgsProcessingParameterFile("LIST_FILE", "Or: identifier list file",
                          fileFilter="Text lists (*.txt *.csv);;All files (*)", optional=True))
        self.addParameter(QgsProcessingParameterEnum("SOURCE", "Default master source",
                          options=["Automatic: all FIX and Navaids", "Navaids: VOR + NDB", "FIX: Enroute + Procedures",
                                   "VOR only", "NDB only", "FIX Enroute only", "FIX Procedures only"], defaultValue=0))
        self.addParameter(QgsProcessingParameterString("BASE_NAME", "Output base name", defaultValue="Selected_Points"))
        self.addParameter(QgsProcessingParameterEnum("FIX_SYMBOL", "FIX symbol",
                          options=list(FIX_SYMBOLS), defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum("UNRESOLVED", "Missing or ambiguous identifiers",
                          options=["Stop and report; write nothing", "Skip and report"], defaultValue=0))
        self.addParameter(QgsProcessingParameterCrs("WORK_CRS", "Working CRS for name offsets (metres)",
                          defaultValue="EPSG:2154"))
        for key, description, value in (("DX", "Name X offset (metres)", 0), ("DY", "Name Y offset (metres; positive = above)", 3500)):
            self.addParameter(QgsProcessingParameterNumber(key, description,
                              QgsProcessingParameterNumber.Double, defaultValue=value))
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT_FOLDER", "New or empty output folder"))
        for key, description in (("SYMBOLS", "Symbols GeoPackage"), ("NAMES", "Names GeoPackage"), ("REPORT", "Import report")):
            self.addOutput(QgsProcessingOutputString(key, description))
        for key, description in (("POINT_COUNT", "Points created"), ("SKIPPED_COUNT", "Unresolved entries"),
                                 ("DUPLICATE_COUNT", "Duplicate entries omitted")):
            self.addOutput(QgsProcessingOutputNumber(key, description))

    def processAlgorithm(self, parameters, context, feedback):
        text = self.parameterAsString(parameters, "POINTS", context).strip()
        file = self.parameterAsFile(parameters, "LIST_FILE", context)
        if text and file:
            raise QgsProcessingException("Use either the pasted list or a list file, not both.")
        if file:
            try:
                text = Path(file).read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as error:
                raise QgsProcessingException("Cannot read UTF-8 list: " + str(error)) from error
        entries = parse_points(text)
        raw_name = self.parameterAsString(parameters, "BASE_NAME", context).strip()
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', "_", raw_name).strip(" ._")
        if (not base or len(base) > 100 or base.lower().startswith(("gpkg_", "sqlite_"))
                or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", base.split(".")[0], re.I)):
            raise QgsProcessingException("Choose a valid output base name (maximum 100 characters).")
        work_crs = self.parameterAsCrs(parameters, "WORK_CRS", context)
        dx = self.parameterAsDouble(parameters, "DX", context)
        dy = self.parameterAsDouble(parameters, "DY", context)
        if not work_crs.isValid() or work_crs.mapUnits() != Qgis.DistanceUnit.Meters or not all(map(math.isfinite, (dx,dy))):
            raise QgsProcessingException("Choose a working CRS in metres and finite name offsets.")
        value = self.parameterAsFileOutput(parameters, "OUTPUT_FOLDER", context)
        if not value:
            raise QgsProcessingException("Choose a new or empty output folder.")
        output = Path(value).resolve()
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise QgsProcessingException("Output folder must be new or empty; existing data is never overwritten.")
        selected, issues, duplicates = resolve_points(
            entries, self.parameterAsEnum(parameters, "SOURCE", context), context, feedback)
        if issues and self.parameterAsEnum(parameters, "UNRESOLVED", context) == 0:
            raise QgsProcessingException(
                "{} unresolved entries. No files written. Correct them or select Skip and report. {}".format(
                    len(issues), "; ".join(p["entry"] + ": " + p["reason"] for p in issues[:10])))
        if not selected:
            raise QgsProcessingException("No unambiguous points were found; no files written.")
        output.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".points-list-", dir=output.parent))
        try:
            svg = QGIS_DIRECTORY / "templates" / "svg"
            if svg.is_dir():
                shutil.copytree(svg, stage / "svg")
            kwargs = dict(stage=stage, output=output, points=selected,
                          fix_symbol=FIX_SYMBOLS[self.parameterAsEnum(parameters,"FIX_SYMBOL",context)],
                          work_crs=work_crs, dx=dx, dy=dy, context=context, feedback=feedback)
            symbols = write_points("symbols", base + "_Symbols", **kwargs)
            feedback.setProgress(45)
            names = write_points("text", base + "_Names", **kwargs)
            report = {
                "input_entries": len(entries), "created_points": len(selected),
                "duplicate_entries": duplicates, "unresolved_entries": issues,
                "working_crs": work_crs.toWkt(), "name_offset_metres": {"x": dx, "y": dy},
                "symbols": symbols, "names": names,
                "points": [{"ident": p["ident"], "source": p["master"], "fid": p["fid"],
                            "longitude": p["point"].x(), "latitude": p["point"].y()} for p in selected],
            }
            (stage / "import_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled; both outputs discarded.")
            had_empty_folder = output.exists()
            if had_empty_folder:
                output.rmdir()
            try:
                os.rename(stage, output)
            except OSError:
                if had_empty_folder and not output.exists():
                    output.mkdir()
                raise
        finally:
            if stage.exists():
                if stage.resolve().parent != output.parent.resolve() or not stage.name.startswith(".points-list-"):
                    raise RuntimeError("Unexpected staging folder.")
                shutil.rmtree(stage)
        if context.project() is not None:
            for filename in (symbols, names):
                context.addLayerToLoadOnCompletion(str(output / filename),
                    QgsProcessingContext.LayerDetails(Path(filename).stem, context.project(), filename))
        feedback.pushInfo("Created {} symbols and {} names; skipped {} entries; omitted {} duplicates. {}".format(
            len(selected), len(selected), len(issues), duplicates, output))
        feedback.setProgress(100)
        return {"OUTPUT_FOLDER": str(output), "SYMBOLS": str(output / symbols), "NAMES": str(output / names),
                "REPORT": str(output / "import_report.json"), "POINT_COUNT": len(selected),
                "SKIPPED_COUNT": len(issues), "DUPLICATE_COUNT": duplicates}
