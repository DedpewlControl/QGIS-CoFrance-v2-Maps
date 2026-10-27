"""QGIS Processing: VOR/NDB symbols and names by perimeter or identifier list."""
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
    QgsDistanceArea, QgsPointXY, QgsProcessing, QgsWkbTypes, QgsProcessingAlgorithm, QgsProcessingContext,
    QgsProcessingException, QgsProcessingOutputNumber, QgsProcessingOutputString,
    QgsProcessingParameterBoolean, QgsProcessingParameterCrs, QgsProcessingParameterEnum, QgsProcessingParameterFile,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber, QgsProcessingParameterString,
)

TOOL_DIRECTORY = Path(r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS\tools")
SOURCE_SETS = (("LFXX_VOR", "LFXX_NDB"), ("LFXX_VOR",), ("LFXX_NDB",))


@lru_cache(maxsize=1)
def shared_tools():
    path = TOOL_DIRECTORY / "generate_navigation_packages.py"
    spec = importlib.util.spec_from_file_location("_cofrance_navaid_geometry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.shared_tools()[0]


@lru_cache(maxsize=1)
def type_tools():
    path = TOOL_DIRECTORY / "navaid_type_lookup.py"
    spec = importlib.util.spec_from_file_location("_cofrance_navaid_types", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def nearby_airport_codes(points, context, feedback):
    """Candidate pages only: proximity never establishes a navaid's type."""
    path = TOOL_DIRECTORY / "cofrance_reference_packages.py"
    spec = importlib.util.spec_from_file_location("_cofrance_navaid_airport_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        layer = module.load_reference("LFXX_Airports", None, context, QgsWkbTypes.PointGeometry)
    except QgsProcessingException as error:
        feedback.pushWarning("Automatic AD 2 lookup unavailable: " + str(error) + " Add supplementary ICAOs in Advanced.")
        return []
    to_wgs = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext())
    airports = []
    for feature in layer.getFeatures():
        if feedback.isCanceled():
            raise QgsProcessingException("Canceled finding supplementary airport pages.")
        code = str(feature["icao"]).strip().upper()
        geometry = feature.geometry()
        if not re.fullmatch(r"[A-Z]{4}", code) or geometry.isNull() or geometry.isEmpty():
            continue
        parts = geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]
        if len(parts) == 1:
            airports.append((code, to_wgs.transform(QgsPointXY(parts[0]))))
    measure = QgsDistanceArea()
    measure.setSourceCrs(QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext())
    measure.setEllipsoid("WGS84")
    candidates = set()
    for point in points:
        distances = sorted((measure.measureLine(point["point"], position), code) for code, position in airports)
        candidates.update(code for distance, code in distances[:3] if distance <= 100000)
    return sorted(candidates)


def perimeter_points(stems, mask, crs, tools, context, feedback):
    selected, duplicates, skipped = [], 0, 0
    for stem in stems:
        layer = tools.load_master(stem, context)
        if layer is None:
            raise QgsProcessingException("Required navaid master is missing: " + stem)
        to_work = QgsCoordinateTransform(layer.crs(), crs, context.transformContext())
        to_wgs = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext())
        for feature in layer.getFeatures():
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled before writing outputs.")
            value = feature["ident"]
            geometry = feature.geometry()
            if value is None or value == NULL or not str(value).strip() or geometry.isNull() or geometry.isEmpty():
                skipped += 1
                continue
            parts = geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]
            if len(parts) != 1:
                raise QgsProcessingException(stem + " fid=" + str(feature.id()) + " must contain exactly one point.")
            point = QgsPointXY(parts[0])
            work = to_work.transform(point)
            point_geometry = QgsGeometry.fromPointXY(work)
            if not mask.intersects(point_geometry) and mask.distance(point_geometry) > 0.000001:
                continue
            ident = str(value).strip().upper()
            if any(other["master"] == stem and other["ident"] == ident and other["work"].distance(work) <= 1
                   for other in selected):
                duplicates += 1
                continue
            selected.append({"ident": ident, "master": stem, "fid": feature.id(),
                             "point": to_wgs.transform(point), "work": work})
    return selected, duplicates, skipped


class GenerateNavaidPackages(QgsProcessingAlgorithm):
    def name(self):
        return "generate_cofrance_navaid_packages"

    def displayName(self):
        return "Generate CoFrance navaid packages"

    def group(self):
        return "CoFrance"

    def groupId(self):
        return "cofrance"

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def createInstance(self):
        return GenerateNavaidPackages()

    def shortHelpString(self):
        return (
            "Choose a polygon perimeter or a manual navaid identifier list. Select VOR + NDB, VOR only or NDB only. "
            "Reads LFXX_VOR/LFXX_NDB automatically from the project or repository navdata. "
            "Cross-checks actual types against the official SIA eAIP facility tables, downloaded once and cached for 24 hours. "
            "Matches identifier, location (default maximum 1000 m) and frequency when available; never infers type from the source layer name. "
            "Automatically checks nearby airports for terminal aids absent from ENR 4.1. Unverified entries are skipped and reported by default; strict mode is available. "
            "A local typed CSV or explicitly unverified legacy mode is also available. "
            "Creates separate Symbols and Names GeoPackages using the CoFrance templates, with VOR/NDB glyphs "
            "and matching semantic name categories. NDB-DME uses overlaid NDB and DME symbols with one name. No airway lines are needed. "
            "Perimeter mode keeps navaids inside or on the polygon, respecting holes and selected perimeter features. "
            "Manual mode accepts e.g. VOR:CNA, NDB:ABC and source-qualified @feature_id for duplicates; "
            "unknown or ambiguous identifiers stop generation before writing. "
            "Advanced contains working CRS and label offsets: EPSG:2154 and 3500 m north by default. "
            "Labels use Arial, Single Labels on text and Offset from point. Offset names can extend outside the perimeter. "
            "Choose a new or empty output folder; reference files and existing outputs are not overwritten."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum("MODE", "Selection method",
            ["Polygon perimeter", "Manual navaid identifier list"], defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource("PERIMETER", "Polygon perimeter",
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterEnum("SOURCES", "Master layers (may contain mixed navaid types)",
            ["LFXX_VOR + LFXX_NDB", "LFXX_VOR only", "LFXX_NDB only"], defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum("TYPE_CHECK", "Type cross-check",
            ["SIA eAIP: selected AIRAC edition (cached)", "Local typed database CSV", "Legacy layer names (no type verification)"], defaultValue=0))
        self.addParameter(QgsProcessingParameterString("IDENTIFIERS", "Manual: navaid identifiers",
            defaultValue="", multiLine=True, optional=True))
        self.addParameter(QgsProcessingParameterString("BASE_NAME", "Map/output base name", defaultValue="Selected"))
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT_FOLDER", "New or empty output folder"))
        self.addParameter(QgsProcessingParameterCrs("WORK_CRS", "Working CRS for perimeter and offsets (metres)", defaultValue="EPSG:2154"))
        for key, label, value in (("DX", "Name X offset (metres)", 0), ("DY", "Name Y offset (metres)", 3500)):
            self.addParameter(QgsProcessingParameterNumber(key, label, QgsProcessingParameterNumber.Double, defaultValue=value))
        self.addParameter(QgsProcessingParameterString("SIA_INDEX", "SIA eAIP edition index URL",
            defaultValue=type_tools().DATABASE_URL))
        self.addParameter(QgsProcessingParameterBoolean("SIA_AUTO_AD", "Automatically check nearby airport AD 2.19 pages", defaultValue=True))
        self.addParameter(QgsProcessingParameterString("SIA_AIRPORTS", "Supplementary SIA AD 2.19 airport ICAOs (optional)", defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterFile("TYPE_CSV", "Local typed navaid CSV", fileFilter="CSV files (*.csv)", optional=True))
        self.addParameter(QgsProcessingParameterNumber("TYPE_DISTANCE", "Maximum type-reference distance (metres)",
            QgsProcessingParameterNumber.Double, defaultValue=1000, minValue=0, maxValue=10000))
        self.addParameter(QgsProcessingParameterBoolean("REFRESH_TYPES", "Force a fresh database download", defaultValue=False))
        self.addParameter(QgsProcessingParameterEnum("TYPE_POLICY", "Unverified or conflicting navaid types",
            ["Stop and report; write nothing", "Skip and report"], defaultValue=1))
        for key in ("WORK_CRS", "DX", "DY", "TYPE_CSV", "TYPE_DISTANCE", "REFRESH_TYPES", "TYPE_POLICY", "SIA_INDEX", "SIA_AIRPORTS", "SIA_AUTO_AD"):
            parameter = self.parameterDefinition(key)
            parameter.setFlags(parameter.flags() | parameter.FlagAdvanced)
        for key in ("SYMBOLS", "NAMES", "REPORT"):
            self.addOutput(QgsProcessingOutputString(key, key.title()))
        for key in ("POINT_COUNT", "DUPLICATE_COUNT", "SKIPPED_COUNT"):
            self.addOutput(QgsProcessingOutputNumber(key, key.replace("_", " ").title()))

    def processAlgorithm(self, parameters, context, feedback):
        navigation, tools = shared_tools()
        crs = self.parameterAsCrs(parameters, "WORK_CRS", context)
        dx, dy = (self.parameterAsDouble(parameters, key, context) for key in ("DX", "DY"))
        if not crs.isValid() or crs.mapUnits() != Qgis.DistanceUnit.Meters or not all(map(math.isfinite, (dx, dy))):
            raise QgsProcessingException("Use a working CRS in metres and finite offsets.")
        source_set = self.parameterAsEnum(parameters, "SOURCES", context)
        stems = SOURCE_SETS[source_set]
        manual = self.parameterAsEnum(parameters, "MODE", context) == 1
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', "_", self.parameterAsString(parameters, "BASE_NAME", context)).strip(" ._")
        if (not base or len(base) > 80 or base.lower().startswith(("gpkg_", "sqlite_"))
                or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", base.split(".")[0], re.I)):
            raise QgsProcessingException("Choose a valid base name (maximum 80 characters).")
        output_value = self.parameterAsFileOutput(parameters, "OUTPUT_FOLDER", context)
        if not output_value:
            raise QgsProcessingException("Choose a new or empty output folder.")
        output = Path(output_value).resolve()
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise QgsProcessingException("Output folder must be new or empty; existing data is never overwritten.")
        mask = None
        if manual:
            entries = tools.parse_points(self.parameterAsString(parameters, "IDENTIFIERS", context))
            for entry in entries:
                qualifier = entry["qualifier"]
                if qualifier and not set(tools.QUALIFIERS[qualifier]).issubset(stems):
                    raise QgsProcessingException("Identifier source is outside the selected navaid types: " + entry["entry"])
            for stem in stems:
                if tools.load_master(stem, context) is None:
                    raise QgsProcessingException("Required navaid master is missing: " + stem)
            selected, issues, duplicates = tools.resolve_points(entries, (1, 3, 4)[source_set], context, feedback)
            if issues:
                raise QgsProcessingException("Unresolved navaids; no files written. " + "; ".join(item["entry"] + ": " + item["reason"] for item in issues))
            skipped = 0
        else:
            source = self.parameterAsSource(parameters, "PERIMETER", context)
            if source is None or not source.sourceCrs().isValid():
                raise QgsProcessingException("Select a polygon perimeter with a defined CRS.")
            mask = navigation.perimeter_geometry(source, crs, context, feedback)
            selected, duplicates, skipped = perimeter_points(stems, mask, crs, tools, context, feedback)
        if not selected:
            raise QgsProcessingException("No navaids match the selection; no files written.")
        type_mode = self.parameterAsEnum(parameters, "TYPE_CHECK", context)
        type_metadata, type_issues = {"mode": "legacy_unverified"}, []
        checker = type_tools()
        if type_mode == 2:
            selected = [dict(point, navaid_type="ndb" if point["master"] == "LFXX_NDB" else "vor",
                             symbol_type="ndb" if point["master"] == "LFXX_NDB" else "vor", type_source="legacy_layer_name_unverified") for point in selected]
            feedback.pushWarning("Legacy mode: navaid types are NOT cross-checked against an external database.")
        else:
            csv_file = self.parameterAsFile(parameters, "TYPE_CSV", context) if type_mode == 1 else ""
            if type_mode == 1 and not csv_file:
                raise QgsProcessingException("Select the local typed database CSV in Advanced.")
            distance = self.parameterAsDouble(parameters, "TYPE_DISTANCE", context)
            if not math.isfinite(distance) or not 0 <= distance <= 10000:
                raise QgsProcessingException("Use a finite type-reference distance between 0 and 10000 metres.")
            database, type_metadata = checker.load_database(csv_file,
                self.parameterAsBool(parameters, "REFRESH_TYPES", context), feedback,
                self.parameterAsString(parameters, "SIA_INDEX", context),
                navigation.tokens(self.parameterAsString(parameters, "SIA_AIRPORTS", context)))
            original = selected
            _, initial_issues, _ = checker.classify_points(original, database, distance, tools, context, feedback, report_issues=False)
            if type_mode == 0 and initial_issues and self.parameterAsBool(parameters, "SIA_AUTO_AD", context):
                missing = {(item["source"], item["fid"]) for item in initial_issues}
                candidates = nearby_airport_codes([point for point in original if (point["master"], point["fid"]) in missing], context, feedback)
                explicit = set(navigation.tokens(self.parameterAsString(parameters, "SIA_AIRPORTS", context)))
                candidates = sorted(set(candidates) - explicit)
                type_metadata["automatic_ad219_airports"] = candidates
                type_metadata["automatic_ad219_pages"] = []
                for code in candidates:
                    if feedback.isCanceled():
                        raise QgsProcessingException("Canceled checking supplementary SIA pages.")
                    try:
                        supplement, metadata = checker.load_terminal_page(code,
                            self.parameterAsString(parameters, "SIA_INDEX", context),
                            self.parameterAsBool(parameters, "REFRESH_TYPES", context), feedback)
                    except QgsProcessingException as error:
                        if feedback.isCanceled():
                            raise
                        metadata = {"airport": code, "mode": "unavailable", "error": str(error)}
                        feedback.pushWarning(code + ": supplementary AD 2 unavailable; " + str(error))
                    else:
                        for ident, records in supplement.items():
                            database.setdefault(ident, []).extend(records)
                    type_metadata["automatic_ad219_pages"].append(metadata)
            selected, type_issues, merged = checker.classify_points(original, database, distance, tools, context, feedback)
            duplicates += merged
            if type_issues and self.parameterAsEnum(parameters, "TYPE_POLICY", context) == 0:
                raise QgsProcessingException("Navaid types could not be verified. No files written. " + "; ".join(
                    item["ident"] + ": " + item["reason"] for item in type_issues[:20]) +
                    (" Some master entries may be obsolete or absent from the selected edition. Use Advanced > Unverified or conflicting navaid types > Skip and report to export verified entries only; supplementary ICAOs can extend the search." if type_mode == 0 else ""))
            if type_issues:
                feedback.pushWarning("Skipped {} unverified source entries; see type_issues in import_report.json. Absence is not proof of withdrawal.".format(len(type_issues)))
            skipped += len(type_issues)
            if not selected:
                raise QgsProcessingException("No navaids have verified types; no files written.")
        symbol_points = checker.symbol_components(selected)
        prefix = base if base.lower().endswith("navaids") else base + "_Navaids"
        output.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".navaid-packages-", dir=output.parent))
        try:
            shutil.copytree(TOOL_DIRECTORY.parent / "templates" / "svg", stage / "svg")
            kwargs = dict(stage=stage, output=output, points=selected, fix_symbol="triangle_hollow",
                          work_crs=crs, dx=dx, dy=dy, context=context, feedback=feedback)
            symbols = tools.write_points("symbols", prefix + "_Symbols", **dict(kwargs, points=symbol_points))
            names = tools.write_points("text", prefix + "_Names", **kwargs)
            report = {"selection_method": "identifier_list" if manual else "perimeter", "masters": list(stems),
                      "created_points": len(selected), "symbol_features": len(symbol_points), "name_features": len(selected),
                      "type_database": type_metadata, "type_issues": type_issues,
                      "duplicate_entries": duplicates, "skipped_source_features": skipped,
                      "name_offset_metres": {"x": dx, "y": dy}, "perimeter_wgs84": None,
                      "points": [{"ident": point["ident"], "source": point["master"], "fid": point["fid"],
                                  "longitude": point["point"].x(), "latitude": point["point"].y(),
                                  "name_category": tools.name_category(point), "navaid_type": point["navaid_type"],
                                  "type_source": point["type_source"], "type_reference_id": point.get("type_reference_id"),
                                  "type_distance_m": point.get("type_distance_m")} for point in selected]}
            if mask is not None:
                geographic = QgsGeometry(mask)
                geographic.transform(QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext()))
                report["perimeter_wgs84"] = json.loads(geographic.asJson())
            (stage / "import_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled; both packages discarded.")
            existed = output.exists()
            if existed:
                output.rmdir()
            try:
                os.rename(stage, output)
            except OSError:
                if existed and not output.exists():
                    output.mkdir()
                raise
        finally:
            if stage.exists():
                if stage.resolve().parent != output.parent.resolve() or not stage.name.startswith(".navaid-packages-"):
                    raise RuntimeError("Unexpected staging directory.")
                shutil.rmtree(stage)
        if context.project() is not None:
            for key, filename in (("SYMBOLS", symbols), ("NAMES", names)):
                context.addLayerToLoadOnCompletion(str(output / filename),
                    QgsProcessingContext.LayerDetails(Path(filename).stem, context.project(), key))
        feedback.pushInfo("Created {} navaid symbols and names; omitted {} duplicates.".format(len(selected), duplicates))
        feedback.setProgress(100)
        return {"OUTPUT_FOLDER": str(output), "SYMBOLS": str(output / symbols), "NAMES": str(output / names),
                "REPORT": str(output / "import_report.json"), "POINT_COUNT": len(selected),
                "DUPLICATE_COUNT": duplicates, "SKIPPED_COUNT": skipped}
