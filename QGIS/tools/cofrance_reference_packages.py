"""Shared airport/runway extraction used by the two public Processing scripts."""
from functools import lru_cache
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from qgis.core import (
    NULL, Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
    QgsPointXY, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingContext,
    QgsProcessingException, QgsProcessingOutputNumber, QgsProcessingOutputString,
    QgsProcessingParameterCrs, QgsProcessingParameterEnum, QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFolderDestination, QgsProcessingParameterNumber, QgsProcessingParameterFile, QgsProcessingParameterBoolean,
    QgsProcessingParameterString, QgsProcessingParameterVectorLayer, QgsVectorLayer, QgsWkbTypes,
)

TOOL_DIRECTORY = Path(r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS\tools")
AIRPORT_SYMBOLS = ("aerodrome", "aerodrome_paved", "aerodrome_ticks", "aerodrome_paved_ticks")


@lru_cache(maxsize=1)
def shared_tools():
    path = TOOL_DIRECTORY / "generate_navigation_packages.py"
    spec = importlib.util.spec_from_file_location("_cofrance_reference_geometry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.shared_tools()[0]


@lru_cache(maxsize=1)
def airport_reference():
    spec = importlib.util.spec_from_file_location("_cofrance_airport_surfaces", TOOL_DIRECTORY / "sia_airport_reference.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_reference(stem, explicit, context, geometry_type):
    if explicit is not None:
        layer = explicit
    else:
        normal = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
        candidates = {}
        for item in context.project().mapLayers().values() if context.project() is not None else []:
            if isinstance(item, QgsVectorLayer) and item.isValid() and (
                    normal(item.name()) == normal(stem)
                    or normal(Path(item.source().split("|")[0]).stem) == normal(stem)):
                candidates[item.source()] = item
        if len(candidates) > 1:
            raise QgsProcessingException("Multiple references match " + stem + "; choose one in Advanced.")
        if candidates:
            layer = next(iter(candidates.values()))
        else:
            qgis = TOOL_DIRECTORY.parent
            paths = [qgis / "navdata" / (stem + ".gpkg"), qgis / "misc" / (stem + ".gpkg"),
                     qgis.parent.parent / "QGIS-Diagrams" / "QGIS" / "src" / "misc" / (stem + ".gpkg")]
            path = next((path for path in paths if path.is_file()), None)
            if path is None:
                raise QgsProcessingException("Load " + stem + " in QGIS, or select its reference in Advanced.")
            options = QgsVectorLayer.LayerOptions()
            options.forceReadOnly = True
            options.loadDefaultStyle = False
            layer = QgsVectorLayer(str(path), stem, "ogr", options)
    if not layer.isValid() or not layer.crs().isValid() or layer.geometryType() != geometry_type or "icao" not in layer.fields().names():
        raise QgsProcessingException(stem + " must have the expected geometry, a defined CRS and an icao field.")
    return layer


class ReferencePackages(QgsProcessingAlgorithm):
    KIND = ""

    def name(self):
        return "generate_cofrance_" + ("airport" if self.KIND == "Airports" else "runway") + "_packages"

    def displayName(self):
        return "Generate CoFrance " + ("airport" if self.KIND == "Airports" else "runway") + " packages"

    def group(self):
        return "CoFrance"

    def groupId(self):
        return "cofrance"

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def createInstance(self):
        return type(self)()

    def shortHelpString(self):
        common = ("Choose a polygon perimeter or a manual airport ICAO list. References are loaded from the project or repository "
                  "automatically, with an optional override in Advanced. Selected perimeter features, polygon holes and source filters "
                  "are respected. Choose a new or empty output folder. Source data and existing outputs are not modified. ")
        if self.KIND == "Airports":
            return common + ("Creates separate airport Symbols and Names GeoPackages from LFXX_Airports. "
                "Automatically selects paved/unpaved ticks from SIA AD 2.12; manual override available. Any paved runway takes priority. "
                "Airport names use the ICAO field, ARP_NAME category and Arial Single Labels on text. "
                "Default text offset is 3500 m north in EPSG:2154; change it in Advanced. "
                "Perimeter selection uses airport reference points; offset names may extend outside the polygon.")
        return common + ("Creates only a Lines GeoPackage from LFXX_Runways. Optional runway-end filters match either rwy1 or rwy2. "
            "Perimeter mode keeps full intersecting runways by default; clipping is optional in Advanced. "
            "Manual ICAO selection keeps complete runway geometry. No symbols or names packages are generated.")

    def initAlgorithm(self, config=None):
        airport = self.KIND == "Airports"
        self.addParameter(QgsProcessingParameterEnum("MODE", "Selection method",
            ["Polygon perimeter", "Manual airport ICAO list"], defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource("PERIMETER", "Polygon perimeter",
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterString("ICAOS", "Manual: airport ICAO list (e.g. LFBD, LFBO)", defaultValue="", multiLine=True, optional=True))
        if airport:
            self.addParameter(QgsProcessingParameterEnum("SYMBOL_MODE", "Airport symbol selection",
                ["Automatic: SIA AD 2.12 runway surfaces", "Manual: use the symbol below"], defaultValue=0))
            self.addParameter(QgsProcessingParameterEnum("SYMBOL", "Manual airport symbol", list(AIRPORT_SYMBOLS), defaultValue=0))
        else:
            self.addParameter(QgsProcessingParameterString("RUNWAYS", "Runway ends (optional; e.g. 05/23)", defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterString("BASE_NAME", "Map/output base name", defaultValue="Selected"))
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT_FOLDER", "New or empty output folder"))
        navigation, _ = shared_tools()
        trajectories = navigation.shared_tools()[1]
        reference = QgsProcessingParameterVectorLayer("REFERENCE", "Optional reference override",
            [QgsProcessing.TypeVectorPoint if airport else QgsProcessing.TypeVectorLine], optional=True)
        reference.setMetadata({"widget_wrapper": {"class": trajectories.SearchableLayerWidgetWrapper}})
        self.addParameter(reference)
        self.addParameter(QgsProcessingParameterCrs("WORK_CRS", "Working CRS for selection and offsets (metres)", defaultValue="EPSG:2154"))
        advanced = ["REFERENCE", "WORK_CRS"]
        if airport:
            for key, value in (("DX", 0), ("DY", 3500)):
                self.addParameter(QgsProcessingParameterNumber(key, "Name " + ("X" if key == "DX" else "Y") + " offset (metres)",
                    QgsProcessingParameterNumber.Double, defaultValue=value))
            self.addParameter(QgsProcessingParameterString("SIA_INDEX", "SIA AIRAC edition index URL", defaultValue=airport_reference().DEFAULT_INDEX))
            self.addParameter(QgsProcessingParameterFile("SIA_HTML_FOLDER", "Optional saved SIA AD 2 HTML folder (automatic mode)",
                behavior=QgsProcessingParameterFile.Folder, optional=True))
            self.addParameter(QgsProcessingParameterBoolean("REFRESH_SURFACES", "Force refresh of SIA runway data", defaultValue=False))
            advanced += ["DX", "DY", "SIA_INDEX", "SIA_HTML_FOLDER", "REFRESH_SURFACES"]
        else:
            self.addParameter(QgsProcessingParameterEnum("CLIP", "Perimeter behaviour",
                ["Keep complete intersecting runways", "Clip lines to perimeter"], defaultValue=0))
            advanced.append("CLIP")
        for key in advanced:
            parameter = self.parameterDefinition(key)
            parameter.setFlags(parameter.flags() | parameter.FlagAdvanced)
        for key in (("SYMBOLS", "NAMES", "REPORT") if airport else ("LINES", "REPORT")):
            self.addOutput(QgsProcessingOutputString(key, key.title()))
        self.addOutput(QgsProcessingOutputNumber("POINT_COUNT" if airport else "LINE_COUNT", "Airports created" if airport else "Runways created"))

    def processAlgorithm(self, parameters, context, feedback):
        navigation, tools = shared_tools()
        airport = self.KIND == "Airports"
        crs = self.parameterAsCrs(parameters, "WORK_CRS", context)
        dx, dy = ((self.parameterAsDouble(parameters, key, context) for key in ("DX", "DY")) if airport else (0, 0))
        if not crs.isValid() or crs.mapUnits() != Qgis.DistanceUnit.Meters or not all(map(math.isfinite, (dx, dy))):
            raise QgsProcessingException("Use a working CRS in metres and finite offsets.")
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', "_", self.parameterAsString(parameters, "BASE_NAME", context)).strip(" ._")
        if (not base or len(base) > 80 or base.lower().startswith(("gpkg_", "sqlite_"))
                or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", base.split(".")[0], re.I)):
            raise QgsProcessingException("Choose a valid base name (maximum 80 characters).")
        value = self.parameterAsFileOutput(parameters, "OUTPUT_FOLDER", context)
        if not value:
            raise QgsProcessingException("Choose a new or empty output folder.")
        output = Path(value).resolve()
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise QgsProcessingException("Output folder must be new or empty; existing data is never overwritten.")
        manual = self.parameterAsEnum(parameters, "MODE", context) == 1
        wanted = set(navigation.tokens(self.parameterAsString(parameters, "ICAOS", context))) if manual else set()
        if manual and (not wanted or any(not re.fullmatch(r"[A-Z0-9]{3,8}", code) for code in wanted)):
            raise QgsProcessingException("Enter airport ICAO identifiers separated by commas, spaces or newlines.")
        mask = None
        if not manual:
            source = self.parameterAsSource(parameters, "PERIMETER", context)
            if source is None or not source.sourceCrs().isValid():
                raise QgsProcessingException("Select a polygon perimeter with a defined CRS.")
            mask = navigation.perimeter_geometry(source, crs, context, feedback)
        stem = "LFXX_Airports" if airport else "LFXX_Runways"
        layer = load_reference(stem, self.parameterAsVectorLayer(parameters, "REFERENCE", context), context,
                               QgsWkbTypes.PointGeometry if airport else QgsWkbTypes.LineGeometry)
        to_work = QgsCoordinateTransform(layer.crs(), crs, context.transformContext())
        to_wgs = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext())
        runway_filter = set() if airport else {navigation.runway(part) for part in navigation.tokens(self.parameterAsString(parameters, "RUNWAYS", context))}
        if any(not re.fullmatch(r"(?:0[1-9]|[12][0-9]|3[0-6])[LRC]?", part) for part in runway_filter):
            raise QgsProcessingException("Use runway ends 01 to 36, optionally L/R/C, separated by commas or /.")
        fields = set(layer.fields().names())
        runway_fields = [field for field in ("rwy1", "rwy2", "runway") if field in fields]
        if runway_filter and not runway_fields:
            raise QgsProcessingException("The runway reference needs rwy1/rwy2 or runway fields for the requested filter.")
        points, rows = [], []
        for feature in layer.getFeatures():
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled before writing outputs.")
            code = navigation.text_value(feature["icao"]).upper()
            if not code or wanted and code not in wanted:
                continue
            geometry = navigation.flat_geometry(feature.geometry())
            if geometry.isNull() or geometry.isEmpty():
                continue
            if airport:
                parts = geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]
                if len(parts) != 1:
                    raise QgsProcessingException("Airport reference fid={} must contain exactly one point.".format(feature.id()))
                point = QgsPointXY(parts[0]); work = to_work.transform(point)
                point_geometry = QgsGeometry.fromPointXY(work)
                if mask is not None and not mask.intersects(point_geometry) and mask.distance(point_geometry) > 0.000001:
                    continue
                if any(other["ident"] == code and other["work"].distance(work) <= 1 for other in points):
                    continue
                if manual and any(other["ident"] == code for other in points):
                    raise QgsProcessingException("Ambiguous airport " + code + "; use a filtered reference with the intended location.")
                points.append({"ident": code, "master": stem, "fid": feature.id(), "point": to_wgs.transform(point),
                               "work": work, "symbol_type": AIRPORT_SYMBOLS[self.parameterAsEnum(parameters, "SYMBOL", context)]})
            else:
                ends = [navigation.runway(feature[field]) for field in runway_fields if navigation.text_value(feature[field])]
                if runway_filter and not runway_filter.intersection(ends):
                    continue
                geometry.transform(to_work)
                rows.append({"name": code + "_RWY_" + ("_".join(ends) or str(feature.id())), "geometry": geometry,
                    "icao": code, "runway": "/".join(ends), "procedures": "", "source": {"layer": layer.name(), "fid": feature.id()},
                    "source_properties": {field.name(): navigation.text_value(feature[field.name()]) for field in layer.fields() if field.name() != "fid"}})
        present = {point["ident"] for point in points} if airport else {row["icao"] for row in rows}
        if wanted - present:
            raise QgsProcessingException("No matching " + self.KIND.lower() + " for: " + ", ".join(sorted(wanted - present)) + ". No files written.")
        if not airport:
            rows = navigation.trim_rows(rows, mask, mask is not None and self.parameterAsEnum(parameters, "CLIP", context) == 1, feedback)
            present = {row["icao"] for row in rows}
        count = len(points) if airport else len(rows)
        if not count:
            raise QgsProcessingException("No " + self.KIND.lower() + " match the selection; no files written.")
        airport_data = []
        if airport and self.parameterAsEnum(parameters, "SYMBOL_MODE", context) == 0:
            for code in sorted(present):
                if feedback.isCanceled():
                    raise QgsProcessingException("Canceled before writing airport packages.")
                airport_data.append(airport_reference().load_airport(code,
                    self.parameterAsString(parameters, "SIA_INDEX", context),
                    self.parameterAsBool(parameters, "REFRESH_SURFACES", context), feedback,
                    self.parameterAsFile(parameters, "SIA_HTML_FOLDER", context)))
            lookup = {item["icao"]: item for item in airport_data}
            for point in points:
                record = lookup[point["ident"]]
                point.update(symbol_type=record["symbol_type"], type_source=record["source_url"],
                    airport_surface="paved" if record["symbol_type"] == "aerodrome_paved_ticks" else "unpaved",
                    runway_length_m=max(row["length_m"] for row in record["runways"]))
        prefix = base if base.lower().endswith(self.KIND.lower()) else base + "_" + self.KIND
        output.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".reference-packages-", dir=output.parent))
        try:
            outputs = {}
            if airport:
                shutil.copytree(TOOL_DIRECTORY.parent / "templates" / "svg", stage / "svg")
                kwargs = dict(stage=stage, output=output, points=points, fix_symbol="triangle_hollow",
                              work_crs=crs, dx=dx, dy=dy, context=context, feedback=feedback)
                # Match the VFR exporter's established ARP naming conventions.
                arp_base = re.sub(r"_ARPs?$", "", base, flags=re.I)
                outputs["SYMBOLS"] = tools.write_points("symbols", arp_base + "_ARPs", **kwargs)
                outputs["NAMES"] = tools.write_points("text", arp_base + "_ARP_Names", **kwargs)
            else:
                outputs["LINES"] = navigation.write_lines(prefix + "_Lines", rows, crs, stage, output, tools, context, feedback)
            report = {"kind": self.KIND, "selection_method": "icao_list" if manual else "perimeter", "reference": layer.source(),
                      "created_features": count, "airport_identifiers": sorted(present), "perimeter_wgs84": None,
                      "clipped": not airport and mask is not None and self.parameterAsEnum(parameters, "CLIP", context) == 1}
            if airport:
                report["name_offset_metres"] = {"x": dx, "y": dy}
                report["airport_symbol_selection"] = "sia_ad_2_12" if airport_data else "manual"
                report["airport_runway_data"] = airport_data
            if mask is not None:
                geographic = QgsGeometry(mask)
                geographic.transform(QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext()))
                report["perimeter_wgs84"] = json.loads(geographic.asJson())
            (stage / "import_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled; all output packages discarded.")
            existed = output.exists()
            if existed:
                output.rmdir()
            try:
                os.rename(stage, output)
            except OSError:
                if existed and not output.exists(): output.mkdir()
                raise
        finally:
            if stage.exists():
                if stage.resolve().parent != output.parent.resolve() or not stage.name.startswith(".reference-packages-"):
                    raise RuntimeError("Unexpected staging directory.")
                shutil.rmtree(stage)
        if context.project() is not None:
            for key, filename in outputs.items():
                context.addLayerToLoadOnCompletion(str(output / filename), QgsProcessingContext.LayerDetails(Path(filename).stem, context.project(), key))
        feedback.pushInfo("Created {} {}.".format(count, self.KIND.lower()))
        feedback.setProgress(100)
        return dict({key: str(output / filename) for key, filename in outputs.items()},
                    OUTPUT_FOLDER=str(output), REPORT=str(output / "import_report.json"), **{"POINT_COUNT" if airport else "LINE_COUNT": count})


class AirportPackages(ReferencePackages):
    KIND = "Airports"


class RunwayPackages(ReferencePackages):
    KIND = "Runways"
