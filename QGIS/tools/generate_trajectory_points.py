"""QGIS Processing: generate symbols and names for any trajectory line layer from line geometry."""

from functools import lru_cache
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QComboBox, QCompleter
from processing.gui.wrappers import MapLayerWidgetWrapper
from qgis.core import (
    NULL, Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFeature, QgsGeometry, QgsPointXY, QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingContext, QgsProcessingException, QgsProcessingOutputNumber,
    QgsProcessingOutputString, QgsProcessingParameterBoolean, QgsProcessingParameterCrs,
    QgsProcessingParameterEnum, QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber, QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer, QgsRectangle, QgsSpatialIndex, QgsVectorLayer,
    QgsWkbTypes,
)

QGIS_DIRECTORY = Path(
    r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS"
)
FIX_SYMBOLS = ("triangle_hollow", "triangle_filled", "circle", "square", "diamond", "point", "cross")
MASTER_SETS = (
    ("LFXX_VOR", "LFXX_NDB", "LFXX_FIX_ENR"),
    ("LFXX_VOR", "LFXX_NDB", "LFXX_FIX_ENR", "LFXX_FIX_PROC"),
    ("LFXX_FIX_ENR",),
)


@lru_cache(maxsize=1)
def package_tools():
    path = QGIS_DIRECTORY / "tools" / "create_points_from_list.py"
    if not path.is_file():
        raise QgsProcessingException("Missing shared package writer: " + str(path) +
                                     ". Keep create_points_from_list.py in the repository's QGIS/tools folder.")
    spec = importlib.util.spec_from_file_location("_cofrance_trajectory_point_writer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SearchableLayerWidgetWrapper(MapLayerWidgetWrapper):
    def createWidget(self):
        widget = super().createWidget()
        if hasattr(self, "combo") and hasattr(self.combo, "setLayer"):
            self.combo.setEditable(True)
            for combo in self.combo.findChildren(QComboBox):
                if combo.completer() is not None:
                    combo.completer().setCaseSensitivity(Qt.CaseInsensitive)
                    combo.completer().setFilterMode(Qt.MatchContains)
                    combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        return widget


def automatic_layer(name, path, context, required=False):
    candidates = {}
    project = context.project()
    for layer in project.mapLayers().values() if project else []:
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            continue
        source = layer.source().split("|")[0]
        if layer.name().casefold() == name.casefold() or source.replace("\\", "/").casefold() == str(path).replace("\\", "/").casefold():
            candidates[layer.source()] = layer
    if len(candidates) > 1:
        raise QgsProcessingException("Multiple different sources match " + name + "; select the intended layer explicitly.")
    if candidates:
        return next(iter(candidates.values()))
    if path.is_file():
        options = QgsVectorLayer.LayerOptions()
        options.forceReadOnly = True
        options.loadDefaultStyle = False
        layer = QgsVectorLayer(str(path), name, "ogr", options)
        if layer.isValid():
            return layer
    if required:
        raise QgsProcessingException("Cannot find " + str(path) + "; select the trajectory layer explicitly.")
    return None


def route_sources(reference, include_dashed, context, feedback):
    layers = [reference]
    if include_dashed and not reference.name().lower().endswith("_dashed"):
        path = Path(reference.source().split("|")[0])
        # The canonical Bordeaux name also works if its displayed layer was renamed.
        name = (path.stem if path.suffix.lower() == ".gpkg" else reference.name()) + "_Dashed"
        companion = automatic_layer(name, path.with_name(name + ".gpkg"), context)
        if companion is not None and companion.source() != reference.source():
            layers.append(companion)
        else:
            feedback.pushInfo("No matching dashed companion found.")
    for layer in layers:
        if not layer.isValid() or not layer.crs().isValid() or layer.geometryType() != QgsWkbTypes.LineGeometry:
            raise QgsProcessingException("A trajectory source must be a line layer with a defined CRS.")
        feedback.pushInfo("Trajectory source: " + layer.name())
    return layers


def index_routes(layers, work_crs, context, feedback):
    routes, vertices = [], []
    route_index, vertex_index = QgsSpatialIndex(), QgsSpatialIndex()
    vertex_keys = {}
    to_wgs = QgsCoordinateTransform(work_crs, QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext())
    for layer in layers:
        transform = QgsCoordinateTransform(layer.crs(), work_crs, context.transformContext())
        for feature in layer.getFeatures():
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled before writing outputs.")
            geometry = QgsGeometry(feature.geometry())
            if geometry.isNull() or geometry.isEmpty():
                feedback.pushWarning("{} fid={}: empty line skipped.".format(layer.name(), feature.id()))
                continue
            geometry.transform(transform)
            index = len(routes)
            ref = {"layer": layer.name(), "fid": feature.id()}
            routes.append({"geometry": geometry, "source": ref})
            indexed = QgsFeature()
            indexed.setId(index)
            indexed.setGeometry(geometry)
            route_index.addFeature(indexed)
            for value in geometry.vertices():
                point = QgsPointXY(value)
                key = (round(point.x(), 3), round(point.y(), 3))
                if key not in vertex_keys:
                    vertex_id = len(vertices)
                    vertex_keys[key] = vertex_id
                    vertices.append({"point": point, "geographic": to_wgs.transform(point), "routes": set()})
                    indexed = QgsFeature()
                    indexed.setId(vertex_id)
                    indexed.setGeometry(QgsGeometry.fromPointXY(point))
                    vertex_index.addFeature(indexed)
                vertices[vertex_keys[key]]["routes"].add(index)
    if not routes:
        raise QgsProcessingException("The selected layers contain no nonempty trajectory lines.")
    return routes, vertices, route_index, vertex_index


def navigation_records(placed, master_set, work_crs, context, feedback):
    tools = package_tools()
    records = []
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    layers = []
    if placed is not None:
        layers.append((placed, placed.name(), True))
    for stem in MASTER_SETS[master_set]:
        layer = tools.load_master(stem, context)
        if layer is None:
            feedback.pushWarning("Unavailable master skipped: " + stem)
        else:
            layers.append((layer, stem, False))
    for layer, stem, is_placed in layers:
        if not layer.crs().isValid() or layer.geometryType() != QgsWkbTypes.PointGeometry:
            raise QgsProcessingException("Point source needs point geometry and a defined CRS: " + stem)
        fields = layer.fields().names()
        name_field = "ident" if "ident" in fields else "name" if is_placed and "name" in fields else None
        if not name_field:
            raise QgsProcessingException("Point source has no ident/name field: " + stem)
        to_work = QgsCoordinateTransform(layer.crs(), work_crs, context.transformContext())
        to_wgs = QgsCoordinateTransform(layer.crs(), wgs, context.transformContext())
        for feature in layer.getFeatures():
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled before writing outputs.")
            name = feature[name_field]
            if name is None or name == NULL or not str(name).strip():
                continue
            geometry = feature.geometry()
            if geometry.isNull() or geometry.isEmpty():
                continue
            points = geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]
            symbol = None
            if is_placed and "symbol_type" in fields and feature["symbol_type"] != NULL:
                symbol = str(feature["symbol_type"]).strip().lower()
                symbol = {"fix_filled": "triangle_filled"}.get(symbol, symbol)
                if symbol and symbol not in set(tools.NAV_SVG) | set(FIX_SYMBOLS):
                    raise QgsProcessingException("Unsupported placed symbol type " + symbol + " in " + stem)
            for value in points:
                point = QgsPointXY(value)
                records.append({"ident": str(name).strip().upper(), "master": stem, "fid": feature.id(),
                                "point": to_wgs.transform(point), "work": to_work.transform(point),
                                "symbol_type": symbol})
    if not records:
        feedback.pushWarning("No named master/placed points are available; vertices will be reported as unmatched.")
    return records


def square(point, radius):
    return QgsRectangle(point.x()-radius, point.y()-radius, point.x()+radius, point.y()+radius)


def match_points(routes, vertices, route_index, vertex_index, records, tolerance, vertices_only, feedback):
    radius = max(tolerance, 0.000001)
    selected = []
    covered = set()
    nearest_index = QgsSpatialIndex()
    for record_id, record in enumerate(records):
        if feedback.isCanceled():
            raise QgsProcessingException("Canceled before writing outputs.")
        point = record["work"]
        indexed = QgsFeature()
        indexed.setId(record_id)
        point_geometry = QgsGeometry.fromPointXY(point)
        indexed.setGeometry(point_geometry)
        nearest_index.addFeature(indexed)
        nearby_vertices = {key for key in vertex_index.intersects(square(point, radius))
                           if vertices[key]["point"].distance(point) <= radius}
        if vertices_only and not nearby_vertices:
            continue
        matched_routes = {key for key in route_index.intersects(square(point, radius))
                          if routes[key]["geometry"].distance(point_geometry) <= radius}
        if not matched_routes:
            continue
        distance = min(routes[key]["geometry"].distance(point_geometry) for key in matched_routes)
        same = next((other for other in selected if other["ident"] == record["ident"]
                     and other["work"].distance(point) <= 1), None)
        if same is None:
            same = dict(record, routes=set(), vertices=set(), distance_m=distance)
            selected.append(same)
        same["routes"].update(matched_routes)
        same["vertices"].update(nearby_vertices)
        same["distance_m"] = min(same["distance_m"], distance)
        covered.update(nearby_vertices)
    unmatched = []
    for index, vertex in enumerate(vertices):
        if index in covered:
            continue
        nearest = nearest_index.nearestNeighbor(vertex["point"], 1) if records else []
        hint = None
        if nearest:
            record = records[nearest[0]]
            hint = {"ident": record["ident"], "source": record["master"],
                    "distance_m": record["work"].distance(vertex["point"])}
        unmatched.append({"vertex_id": index, "longitude": vertex["geographic"].x(),
                          "latitude": vertex["geographic"].y(),
                          "routes": [routes[key]["source"] for key in sorted(vertex["routes"])],
                          "nearest_hint_only": hint})
    return selected, unmatched


class GenerateTrajectoryPoints(QgsProcessingAlgorithm):
    def name(self):
        return "generate_cofrance_trajectory_points"

    def displayName(self):
        return "Generate CoFrance trajectory points and names"

    def group(self):
        return "CoFrance"

    def groupId(self):
        return "cofrance"

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def createInstance(self):
        return GenerateTrajectoryPoints()

    def shortHelpString(self):
        return (
            "Select any FRA, airway or procedure trajectory line layer, with an optional dashed companion. "
            "Finds VOR/NDB/FIX masters on trajectory segments or vertices, and creates separate symbols and names GeoPackages. "
            "An optional selected point layer supplies existing names and symbol types. "
            "Shared points are created once. Same identifiers within 1 metre are merged; existing placed symbols take priority. "
            "No navigation name is invented for unmatched vertices: they are reported, with optional UNNAMED placeholders. "
            "Default matching tolerance is 5 metres in EPSG:2154. Names default to a 3500-metre northward offset. "
            "Use a new or empty output folder. The source trajectories and existing point packages are not edited. "
            "Uses create_points_from_list.py from this repository as the package writer; only this script needs installing."
        )

    def initAlgorithm(self, config=None):
        for key, label, kind in (
                ("TRAJECTORIES", "Trajectory line layer", QgsProcessing.TypeVectorLine),
                ("PLACED_POINTS", "Optional existing named symbols", QgsProcessing.TypeVectorPoint)):
            parameter = QgsProcessingParameterVectorLayer(key, label, [kind], optional=key!="TRAJECTORIES")
            parameter.setMetadata({"widget_wrapper": {"class": SearchableLayerWidgetWrapper}})
            self.addParameter(parameter)
        self.addParameter(QgsProcessingParameterBoolean("DASHED", "Include matching _Dashed trajectories", defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean("USE_PLACED", "Use the optional existing named symbols layer", defaultValue=False))
        self.addParameter(QgsProcessingParameterEnum("MASTER_SET", "Navigation masters",
                          options=["Enroute FIX + VOR + NDB", "Enroute/Procedures FIX + VOR + NDB", "Enroute FIX only"], defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum("MATCH_MODE", "Detect points",
                          options=["Anywhere on lines, including vertices", "At line vertices only"], defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean("UNNAMED", "Create UNNAMED placeholders at unmatched vertices", defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber("TOLERANCE", "Matching tolerance (metres)",
                          QgsProcessingParameterNumber.Double, defaultValue=5, minValue=0, maxValue=1000))
        self.addParameter(QgsProcessingParameterEnum("FIX_SYMBOL", "Symbol for new FIX/unnamed points",
                          options=list(FIX_SYMBOLS), defaultValue=0))
        self.addParameter(QgsProcessingParameterString("BASE_NAME", "Output base name", defaultValue="Trajectory_Points"))
        self.addParameter(QgsProcessingParameterCrs("WORK_CRS", "Working CRS (metres)", defaultValue="EPSG:2154"))
        for key, label, value in (("DX", "Name X offset (metres)", 0), ("DY", "Name Y offset (metres)", 3500)):
            self.addParameter(QgsProcessingParameterNumber(key, label, QgsProcessingParameterNumber.Double, defaultValue=value))
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT_FOLDER", "New or empty output folder"))
        for key in ("SYMBOLS", "NAMES", "REPORT"):
            self.addOutput(QgsProcessingOutputString(key, key.title()))
        for key, label in (("POINT_COUNT", "Total generated points"), ("MATCHED_COUNT", "Named points found"),
                           ("UNNAMED_COUNT", "Placeholder points created"), ("UNMATCHED_VERTEX_COUNT", "Unmatched vertices"),
                           ("LINE_COUNT", "Trajectory features"), ("VERTEX_COUNT", "Distinct line vertices")):
            self.addOutput(QgsProcessingOutputNumber(key, label))

    def processAlgorithm(self, parameters, context, feedback):
        reference = self.parameterAsVectorLayer(parameters, "TRAJECTORIES", context)
        if reference is None:
            raise QgsProcessingException("Select a trajectory line layer.")
        layers = route_sources(reference, self.parameterAsBool(parameters,"DASHED",context), context, feedback)
        placed = None
        if self.parameterAsBool(parameters,"USE_PLACED",context):
            placed = self.parameterAsVectorLayer(parameters,"PLACED_POINTS",context)
            if placed is None:
                raise QgsProcessingException("Select an existing named point layer, or disable its use.")
        crs = self.parameterAsCrs(parameters,"WORK_CRS",context)
        dx,dy = (self.parameterAsDouble(parameters,key,context) for key in ("DX","DY"))
        tolerance = self.parameterAsDouble(parameters,"TOLERANCE",context)
        if not crs.isValid() or crs.mapUnits()!=Qgis.DistanceUnit.Meters or not all(map(math.isfinite,(dx,dy,tolerance))):
            raise QgsProcessingException("Use a working CRS in metres and finite offsets/tolerance.")
        raw_base = self.parameterAsString(parameters,"BASE_NAME",context).strip()
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+',"_",raw_base).strip(" ._")
        if (not base or len(base)>100 or base.lower().startswith(("gpkg_","sqlite_"))
                or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]",base.split(".")[0],re.I)):
            raise QgsProcessingException("Choose a valid base name (maximum 100 characters).")
        value = self.parameterAsFileOutput(parameters,"OUTPUT_FOLDER",context)
        if not value:
            raise QgsProcessingException("Choose a new or empty output folder.")
        output = Path(value).resolve()
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise QgsProcessingException("Output folder must be new or empty; existing packages are preserved.")
        routes,vertices,route_index,vertex_index = index_routes(layers,crs,context,feedback)
        records = navigation_records(placed,self.parameterAsEnum(parameters,"MASTER_SET",context),crs,context,feedback)
        selected,unmatched = match_points(routes,vertices,route_index,vertex_index,records,tolerance,
                                         self.parameterAsEnum(parameters,"MATCH_MODE",context)==1,feedback)
        named_count = len(selected)
        unnamed_count = 0
        if self.parameterAsBool(parameters,"UNNAMED",context):
            for number,vertex in enumerate(unmatched,1):
                info = vertices[vertex["vertex_id"]]
                selected.append({"ident":"UNNAMED_"+str(number).zfill(3), "master":"Unmatched trajectory vertex",
                                 "fid":vertex["vertex_id"],"point":info["geographic"],"work":info["point"],
                                 "routes":set(info["routes"]),"vertices":{vertex["vertex_id"]},"distance_m":0})
                unnamed_count += 1
        if not selected:
            raise QgsProcessingException("No named navigation points match the trajectories. Check the CRS/tolerance, "
                                         "or enable UNNAMED placeholders for unmatched vertices.")
        tools = package_tools()
        output.parent.mkdir(parents=True,exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".trajectory-points-",dir=output.parent))
        try:
            shutil.copytree(QGIS_DIRECTORY/"templates"/"svg",stage/"svg")
            kwargs = dict(stage=stage,output=output,points=selected,
                          fix_symbol=FIX_SYMBOLS[self.parameterAsEnum(parameters,"FIX_SYMBOL",context)],
                          work_crs=crs,dx=dx,dy=dy,context=context,feedback=feedback)
            symbols = tools.write_points("symbols",base+"_Symbols",**kwargs)
            names = tools.write_points("text",base+"_Names",**kwargs)
            report = {
                "trajectory_sources":[{"name":layer.name(),"source":layer.source()} for layer in layers],
                "existing_point_source":placed.source() if placed is not None else None,
                "line_features":len(routes),"distinct_vertices":len(vertices),
                "named_points":named_count,"placeholder_points":unnamed_count,
                "unmatched_vertices":unmatched,"tolerance_metres":tolerance,
                "match_mode":"vertices" if self.parameterAsEnum(parameters,"MATCH_MODE",context) else "lines",
                "working_crs":crs.toWkt(),"name_offset_metres":{"x":dx,"y":dy},
                "points":[{"ident":point["ident"],"source":point["master"],"fid":point["fid"],
                           "longitude":point["point"].x(),"latitude":point["point"].y(),
                           "distance_to_line_metres":point["distance_m"],
                           "routes":[routes[key]["source"] for key in sorted(point["routes"])]}
                          for point in selected],
                "notes":["Matching uses planar distances in the working CRS.",
                         "Intersections without a stored vertex or named navigation point are not invented.",
                         "Nearest-master hints do not assign a navigation name to an unmatched vertex.",
                         "Route activation/zoom rules are not inferred; generated fields can be edited separately."],
            }
            (stage/"import_report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled; both outputs discarded.")
            was_empty = output.exists()
            if was_empty:
                output.rmdir()
            try:
                os.rename(stage,output)
            except OSError:
                if was_empty and not output.exists():
                    output.mkdir()
                raise
        finally:
            if stage.exists():
                if stage.resolve().parent!=output.parent.resolve() or not stage.name.startswith(".trajectory-points-"):
                    raise RuntimeError("Unexpected staging folder.")
                shutil.rmtree(stage)
        if context.project() is not None:
            for key,filename in (("SYMBOLS",symbols),("NAMES",names)):
                context.addLayerToLoadOnCompletion(str(output/filename),
                    QgsProcessingContext.LayerDetails(Path(filename).stem,context.project(),key))
        feedback.pushInfo("Read {} lines / {} distinct vertices; found {} named points, {} unmatched vertices; "
                          "created {} placeholders.".format(len(routes),len(vertices),named_count,len(unmatched),unnamed_count))
        if unmatched:
            feedback.pushWarning("Unmatched vertex coordinates and nearest hints are in import_report.json.")
        feedback.setProgress(100)
        return {"OUTPUT_FOLDER":str(output),"SYMBOLS":str(output/symbols),"NAMES":str(output/names),
                "REPORT":str(output/"import_report.json"),"POINT_COUNT":len(selected),"MATCHED_COUNT":named_count,
                "UNNAMED_COUNT":unnamed_count,"UNMATCHED_VERTEX_COUNT":len(unmatched),
                "LINE_COUNT":len(routes),"VERTEX_COUNT":len(vertices)}
