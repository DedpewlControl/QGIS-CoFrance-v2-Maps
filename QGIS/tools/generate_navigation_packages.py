"""QGIS Processing: airways by list/perimeter; STAR/SID by airport and runway."""

from contextlib import closing
from functools import lru_cache
import fnmatch
import gc
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtXml import QDomDocument
from qgis.core import (
    NULL, Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFeature, QgsField, QgsGeometry, QgsLineSymbol, QgsPointXY, QgsProcessing,
    QgsProcessingAlgorithm, QgsProcessingContext, QgsProcessingException,
    QgsProcessingOutputNumber, QgsProcessingOutputString,
    QgsProcessingParameterBoolean, QgsProcessingParameterCrs, QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber, QgsProcessingParameterString, QgsProcessingParameterVectorLayer,
    QgsSingleSymbolRenderer, QgsVectorLayer, QgsWkbTypes,
)

QGIS_DIRECTORY = Path(r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS")
KINDS = ("HIGH_AIRWAYS", "LOW_AIRWAYS", "STAR", "SID", "STAR", "SID")
REFERENCE_NAMES = ("LFXX_HIGH_AWY", "LFXX_LOW_AWY", "LFXX_STAR", "LFXX_SID")


@lru_cache(maxsize=1)
def shared_tools():
    def load(filename, alias):
        path = QGIS_DIRECTORY/"tools"/filename
        if not path.is_file():
            raise QgsProcessingException("Missing shared tool: " + str(path))
        spec = importlib.util.spec_from_file_location(alias, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    points = load("create_points_from_list.py", "_cofrance_navigation_writer")
    trajectories = load("generate_trajectory_points.py", "_cofrance_navigation_matching")
    return points, trajectories


def text_value(value):
    return "" if value is None or value == NULL else str(value).strip()


def tokens(value):
    return [part.upper() for part in re.split(r"[,;+\s/]+", value.strip()) if part]


def procedure_configuration(icao_text, runway_text, manual=False):
    airports=list(dict.fromkeys(tokens(icao_text)))
    runways=list(dict.fromkeys(runway(part) for part in tokens(runway_text)))
    if not airports:
        raise QgsProcessingException("Enter an airport ICAO for STAR/SID generation.")
    if any(not re.fullmatch(r"[A-Z]{4}", airport) for airport in airports):
        raise QgsProcessingException("Airport ICAO codes must contain four letters, e.g. LFBD.")
    if manual and len(airports)!=1:
        raise QgsProcessingException("A manual procedure requires one airport ICAO.")
    if not runways:
        raise QgsProcessingException("Enter the runway configuration for STAR/SID generation, e.g. 23 or 14L/14R.")
    if any(not re.fullmatch(r"(?:0[1-9]|[12][0-9]|3[0-6])[LRC]?", value) for value in runways):
        raise QgsProcessingException("Use runway numbers 01 to 36, optionally L/R/C, separated by commas, spaces or /.")
    return airports,runways


def normalized_procedure(value):
    value = re.sub(r"[\s_-]+", "", value.upper()).replace("RWY", "")
    # The bundled STAR reference labels these RNAV, not RNP.
    return "RNAV"+value[3:] if value.startswith("RNP") else value


def runway(value):
    value = re.sub(r"^(?:RWY|RW)", "", text_value(value).upper())
    match = re.fullmatch(r"(\d{1,2})([LRC]?)", value)
    return str(int(match[1])).zfill(2)+match[2] if match else value


def flat_geometry(geometry):
    geometry = QgsGeometry(geometry)
    if not geometry.isNull():
        if QgsWkbTypes.hasZ(geometry.wkbType()):
            geometry.get().dropZValue()
        if QgsWkbTypes.hasM(geometry.wkbType()):
            geometry.get().dropMValue()
    return geometry


def perimeter_geometry(source, work_crs, context, feedback):
    transform = QgsCoordinateTransform(source.sourceCrs(), work_crs, context.transformContext())
    parts = []
    for feature in source.getFeatures():
        if feedback.isCanceled():
            raise QgsProcessingException("Canceled before writing outputs.")
        geometry = flat_geometry(feature.geometry())
        if geometry.isNull() or geometry.isEmpty():
            continue
        geometry.transform(transform)
        if not geometry.isGeosValid():
            raise QgsProcessingException("Invalid perimeter polygon fid={}; repair its geometry first.".format(feature.id()))
        parts.append(geometry)
    if not parts:
        raise QgsProcessingException("The perimeter contains no nonempty polygons (check its selected features/filter).")
    geometry = QgsGeometry.unaryUnion(parts)
    if geometry.isNull() or geometry.isEmpty() or not geometry.isGeosValid():
        raise QgsProcessingException("Cannot combine the perimeter polygons.")
    return geometry


def line_parts(geometry):
    if geometry.isNull() or geometry.isEmpty():
        return []
    if geometry.type() == QgsWkbTypes.LineGeometry:
        return geometry.asGeometryCollection() if geometry.isMultipart() else [geometry]
    parts = []
    if QgsWkbTypes.flatType(geometry.wkbType()) == QgsWkbTypes.GeometryCollection:
        for child in geometry.asGeometryCollection():
            parts.extend(line_parts(child))
    return parts


def trim_rows(rows, mask, clipped, feedback):
    selected = []
    for row in rows:
        if feedback.isCanceled():
            raise QgsProcessingException("Canceled before writing outputs.")
        geometry = row["geometry"]
        if mask is not None and not mask.intersects(geometry):
            continue
        result = geometry.intersection(mask) if mask is not None and clipped else QgsGeometry(geometry)
        if result.isNull():
            raise QgsProcessingException("Intersection failed for " + row["name"] + ": " + result.lastError())
        parts = [part for part in line_parts(result) if part.length() > 0.000001]
        if not parts:
            continue
        geometry = QgsGeometry.collectGeometry(parts)
        geometry.convertToMultiType()
        selected.append(dict(row,geometry=geometry))
    if not selected:
        raise QgsProcessingException("No nonempty line geometry matches the requested filters/selection.")
    return selected


def load_reference(stem, explicit, context):
    if explicit is not None:
        layer = explicit
    else:
        _, trajectories = shared_tools()
        layer = trajectories.automatic_layer(stem,QGIS_DIRECTORY/"navdata"/(stem+".gpkg"),context,True)
    if not layer.isValid() or not layer.crs().isValid() or layer.geometryType()!=QgsWkbTypes.LineGeometry:
        raise QgsProcessingException("The reference must be a line layer with a defined CRS.")
    return layer


def reference_rows(layer, mode, work_crs, icaos, runways, procedures, airways, context, feedback):
    fields = set(layer.fields().names())
    airport_field = next((field for field in ("icao","airport","airport_icao") if field in fields),None)
    runway_field = next((field for field in ("runway","rwy") if field in fields),None)
    procedure_field = next((field for field in (
        ("sid","procedure","procedure_name","name") if mode==3 else
        ("star","procedure","procedure_name","procedures","name")) if field in fields),None)
    if mode < 2 and "airway" not in fields:
        raise QgsProcessingException("The airway reference requires an airway field.")
    for values, field, label in ((icaos,airport_field,"airport"),(runways,runway_field,"runway"),(procedures,procedure_field,"procedure")):
        if mode >= 2 and values and not field:
            raise QgsProcessingException("This reference has no " + label + " field for the requested filter.")
    grouped = mode==2 and procedure_field=="procedures"
    if grouped:
        feedback.pushInfo("STAR reference stores grouped approach labels; individual STAR identities or merged parallel-runway geometry cannot be separated.")
    transform = QgsCoordinateTransform(layer.crs(),work_crs,context.transformContext())
    rows = []
    for feature in layer.getFeatures():
        if feedback.isCanceled():
            raise QgsProcessingException("Canceled before writing outputs.")
        attrs = {field.name():text_value(feature[field.name()]) for field in layer.fields() if field.name()!="fid"}
        airport = text_value(feature[airport_field]).upper() if airport_field else ""
        rwy = runway(feature[runway_field]) if runway_field else ""
        proc = text_value(feature[procedure_field]) if procedure_field else ""
        if mode < 2:
            name = text_value(feature["airway"]).upper()
            if airways and not any(fnmatch.fnmatchcase(name,pattern) for pattern in airways):
                continue
        else:
            if icaos and airport not in icaos:
                continue
            values = [normalized_procedure(part) for part in re.split(r"[;,]",proc) if part.strip()]
            runway_match = not runways or rwy in runways
            if grouped and runways:
                # STAR's numeric runway field loses L/R/C. Its approach labels
                # retain sides, but geometry for those sides is already merged.
                labelled_runways = {match.group(1) for value in values for match in
                    [re.match(r"(?:ILS|RNAV|LOC|VOR(?:DME)?|NDB(?:DME)?)([0-9]{2}[LRC]?)",value)] if match}
                runway_match = runway_match or any(requested in labelled_runways for requested in runways)
                # A runway-independent STAR group is common to the airport.
                runway_match = runway_match or (not rwy and "STAR" in values)
            if not runway_match:
                continue
            patterns = [normalized_procedure(part) for part in procedures]
            if patterns and not any(fnmatch.fnmatchcase(value,pattern)
                                    or (pattern in ("ILS","RNAV","LOC","VOR","NDB") and value.startswith(pattern))
                                    for value in values for pattern in patterns):
                continue
            name = "_".join(part for part in (KINDS[mode],airport,rwy,proc) if part)
        geometry = flat_geometry(feature.geometry())
        if geometry.isNull() or geometry.isEmpty():
            continue
        geometry.transform(transform)
        rows.append({"name":name or KINDS[mode],"geometry":geometry,"icao":airport,"runway":rwy,
                     "procedures":proc,"source":{"layer":layer.name(),"fid":feature.id()},
                     "source_properties":attrs})
    if not rows:
        message = "No reference features match the requested filters."
        if grouped:
            message += " This STAR file has approach groups, not individual named STARs; use a group label or Manual STAR."
        raise QgsProcessingException(message)
    return rows,grouped


def resolve_manual(text, records, kind, tools):
    cleaned = "\n".join(line.split("#",1)[0].strip() for line in text.splitlines())
    nonempty = [line for line in cleaned.splitlines() if line]
    if not nonempty:
        raise QgsProcessingException("Enter an ordered point list for the manual procedure.")
    if any("|" in line for line in nonempty):
        if any("|" not in line for line in nonempty):
            raise QgsProcessingException("For named manual routes, use one route per line: Route name | POINT1 > POINT2.")
        groups = [line.split("|",1) for line in nonempty]
    else:
        groups = [(kind+"_Manual_"+str(i).zfill(2),block) for i,block in enumerate(
            (block for block in re.split(r"\n\s*\n",cleaned) if block.strip()),1)]
    rows,chosen = [],[]
    for route_name, body in groups:
        if not route_name.strip():
            raise QgsProcessingException("Enter a name before the manual route separator |.")
        entries = tools.parse_points(body.replace("->"," ").replace(">"," "))
        ordered = []
        for entry in entries:
            stems = tools.QUALIFIERS[entry["qualifier"]] if entry["qualifier"] else tools.SOURCES
            matches = [record for record in records if record["ident"]==entry["ident"]
                       and (entry["qualifier"] is None or record["master"] in stems)
                       and (entry["fid"] is None or record["fid"]==entry["fid"])]
            distinct = []
            for match in matches:
                if not any(other["work"].distance(match["work"])<=1 for other in distinct):
                    distinct.append(match)
            if len(distinct)!=1:
                choices = "; ".join("{}:{}@{}".format(record["master"],record["ident"],record["fid"]) for record in distinct)
                raise QgsProcessingException("Manual point {} is {}. {} No procedure was written.".format(
                    entry["entry"],"not found" if not distinct else "ambiguous",choices))
            point = distinct[0]
            if not ordered or ordered[-1]["work"].distance(point["work"])>0.000001:
                ordered.append(point)
            chosen.append(point)
        if len(ordered)<2:
            raise QgsProcessingException("Manual route " + route_name + " needs at least two distinct point locations.")
        rows.append({"name":route_name.strip(),"geometry":QgsGeometry.fromPolylineXY([p["work"] for p in ordered]),
                     "icao":"","runway":"","procedures":route_name.strip(),
                     "source":{"layer":"Manual "+kind,"fid":len(rows)+1},
                     "source_properties":{"ordered_entries":[entry["entry"] for entry in entries]}})
    return rows,chosen


def matching_layer(rows,crs):
    layer = QgsVectorLayer("MultiLineString","Selected routes","memory")
    layer.setCrs(crs)
    for row in rows:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(row["geometry"])
        if not layer.dataProvider().addFeatures([feature])[0]:
            raise QgsProcessingException("Cannot prepare selected routes.")
    return layer


def write_lines(name,rows,work_crs,stage,output,tools,context,feedback):
    path = stage/(name+".gpkg")
    tools.clone_template("lines",path,name)
    layer = None
    try:
        layer = QgsVectorLayer(str(path)+"|layername="+name,name,"ogr")
        if not layer.isValid() or not layer.startEditing():
            raise QgsProcessingException("Cannot edit the line output.")
        for field in ("route_name","icao","runway","procedures","source_reference","source_properties"):
            if not layer.addAttribute(QgsField(field,QVariant.String)):
                raise QgsProcessingException("Cannot add line metadata.")
        layer.addAttribute(QgsField("source_fid",QVariant.LongLong))
        transform = QgsCoordinateTransform(work_crs,layer.crs(),context.transformContext())
        for row in rows:
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled; all outputs discarded.")
            feature = QgsFeature(layer.fields())
            geometry = QgsGeometry(row["geometry"])
            geometry.transform(transform)
            geometry.convertToMultiType()
            feature.setGeometry(geometry)
            feature["name"] = row["name"]
            feature["route_name"] = row["name"]
            for field in ("icao","runway","procedures"):
                feature[field]=row[field]
            feature["source_reference"]=row["source"]["layer"]
            feature["source_fid"]=row["source"]["fid"]
            feature["source_properties"]=json.dumps(row["source_properties"],ensure_ascii=False)
            for field in layer.fields():
                if field.name().startswith("activation_") or field.name() in ("z_index","zoomin","zoomout"):
                    value=row["source_properties"].get(field.name())
                    if value not in (None,""):
                        feature[field.name()]=value
            if not layer.addFeature(feature):
                raise QgsProcessingException("Cannot insert line " + row["name"])
        if not layer.commitChanges():
            raise QgsProcessingException("Cannot save lines: "+"; ".join(layer.commitErrors()))
        symbol = QgsLineSymbol.createSimple({"line_color":"160,160,160","line_width":"1"})
        symbol.symbolLayer(0).setWidthUnit(Qgis.RenderUnit.Pixels)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.setLabelsEnabled(False)
        document = QDomDocument()
        error = layer.exportNamedStyle(document)
        if error:
            raise QgsProcessingException(error)
        qml = tools.style_xml(document,stage,output)
    finally:
        if layer is not None:
            if layer.isEditable():
                layer.rollBack()
            sip.delete(layer)
        layer=None
        gc.collect()
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE layer_styles SET styleQML=?,useAsDefault=1 WHERE f_table_name=?",(qml,name))
        db.commit()
    return path.name


class GenerateNavigationPackages(QgsProcessingAlgorithm):
    def name(self):
        return "generate_cofrance_navigation_packages"

    def displayName(self):
        return "Generate CoFrance airways, STAR or SID packages"

    def group(self):
        return "CoFrance"

    def groupId(self):
        return "cofrance"

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def createInstance(self):
        return GenerateNavigationPackages()

    def shortHelpString(self):
        return (
            "For high/low airways choose Polygon perimeter or Manual airway identifier list. "
            "The list mode loads complete named airways without requiring a polygon. "
            "STAR/SID generation requires airport ICAO and runway configuration, with optional reference procedure filters "
            "or the existing ordered manual point-list modes. Procedure generation does not use the perimeter. "
            "Writes Lines, Symbols and Names GeoPackages into a new or empty folder. "
            "Airway perimeter mode clips lines and limits point anchors to the polygon by default, retaining holes. "
            "Other modes keep the complete selected routes and their named points. "
            "Reference filters support airports, runways and procedure/airway identifiers (wildcards allowed). "
            "The bundled STAR reference has grouped approach labels such as ILS23/RNAV23, not individual STAR identifiers. "
            "RNP filter spelling maps to its RNAV labels; grouped geometry is not separated into individual procedures. "
            "For manual routes enter ordered navigation names; optionally use Route name | A > B > C, one branch per line. "
            "Unknown/ambiguous manual names stop the run; missing names are never bridged. "
            "Names use FIX_NAME/VOR_NAME/NDB_NAME categories, Arial, and the adjustable 3500-metre upward offset. "
            "Unmatched vertices are reported, with optional UNNAMED placeholders. "
            "This is a cartographic extraction; no ARINC turn, altitude or protected-area calculation is performed."
        )

    def initAlgorithm(self,config=None):
        self.addParameter(QgsProcessingParameterFeatureSource("PERIMETER","Airways: polygon perimeter",
                          [QgsProcessing.TypeVectorPolygon],optional=True))
        self.addParameter(QgsProcessingParameterEnum("MODE","Generate",
                          options=["High airways","Low airways","STAR from reference","SID from reference",
                                   "Manual STAR from ordered points","Manual SID from ordered points"],defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum("AIRWAY_SELECTION","Airways: selection method",
                          options=["Polygon perimeter","Manual airway identifier list"],defaultValue=0))
        parameter=QgsProcessingParameterVectorLayer("REFERENCE","Optional alternative line reference",
                          [QgsProcessing.TypeVectorLine],optional=True)
        _,trajectories=shared_tools()
        parameter.setMetadata({"widget_wrapper":{"class":trajectories.SearchableLayerWidgetWrapper}})
        self.addParameter(parameter)
        self.addParameter(QgsProcessingParameterString("ICAO","STAR/SID: airport ICAO (required)",defaultValue="",optional=True))
        self.addParameter(QgsProcessingParameterString("RUNWAYS","STAR/SID: runway configuration (required; e.g. 23 or 14L/14R)",defaultValue="",optional=True))
        self.addParameter(QgsProcessingParameterString("PROCEDURES","Reference STAR/SID: names or labels (e.g. ILS23, RNP23, STAR)",defaultValue="",optional=True))
        self.addParameter(QgsProcessingParameterString("AIRWAYS","Airways: identifiers (required in list mode; optional perimeter filter)",defaultValue="",optional=True))
        self.addParameter(QgsProcessingParameterString("MANUAL_POINTS","Manual procedure: ordered points / named branches",
                          defaultValue="",multiLine=True,optional=True))
        self.addParameter(QgsProcessingParameterVectorLayer("POINT_REFERENCE","Optional additional named point source",
                          [QgsProcessing.TypeVectorPoint],optional=True))
        self.addParameter(QgsProcessingParameterEnum("CLIP","Airways: perimeter behaviour",
                          options=["Clip lines to perimeter","Keep complete intersecting reference features"],defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean("UNNAMED","Create UNNAMED symbols/labels for unmatched vertices",defaultValue=False))
        self.addParameter(QgsProcessingParameterString("BASE_NAME","Map/output base name",defaultValue="Navigation"))
        self.addParameter(QgsProcessingParameterCrs("WORK_CRS","Working CRS for clipping/matching/offsets (metres)",defaultValue="EPSG:2154"))
        self.addParameter(QgsProcessingParameterNumber("TOLERANCE","Point-to-line matching tolerance (metres)",
                          QgsProcessingParameterNumber.Double,defaultValue=5,minValue=0,maxValue=1000))
        for key,label,value in (("DX","Name X offset (metres)",0),("DY","Name Y offset (metres)",3500)):
            self.addParameter(QgsProcessingParameterNumber(key,label,QgsProcessingParameterNumber.Double,defaultValue=value))
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT_FOLDER","New or empty output folder"))
        for key in ("LINES","SYMBOLS","NAMES","REPORT"):
            self.addOutput(QgsProcessingOutputString(key,key.title()))
        for key in ("LINE_COUNT","POINT_COUNT","UNMATCHED_VERTEX_COUNT"):
            self.addOutput(QgsProcessingOutputNumber(key,key.replace("_"," ").title()))

    def processAlgorithm(self,parameters,context,feedback):
        tools,trajectories=shared_tools()
        mode=self.parameterAsEnum(parameters,"MODE",context)
        kind=KINDS[mode]
        crs=self.parameterAsCrs(parameters,"WORK_CRS",context)
        dx,dy,tolerance=(self.parameterAsDouble(parameters,key,context) for key in ("DX","DY","TOLERANCE"))
        if not crs.isValid() or crs.mapUnits()!=Qgis.DistanceUnit.Meters or not all(map(math.isfinite,(dx,dy,tolerance))):
            raise QgsProcessingException("Use a CRS in metres and finite offsets/tolerance.")
        airway_list=mode<2 and self.parameterAsEnum(parameters,"AIRWAY_SELECTION",context)==1
        use_perimeter=mode<2 and not airway_list
        airway_filters=tokens(self.parameterAsString(parameters,"AIRWAYS",context))
        airports,runway_config=[],[]
        if mode>=2:
            airports,runway_config=procedure_configuration(
                self.parameterAsString(parameters,"ICAO",context),
                self.parameterAsString(parameters,"RUNWAYS",context),manual=mode>=4)
        elif airway_list and not airway_filters:
            raise QgsProcessingException("Enter at least one airway identifier for manual airway list mode.")
        mask=None
        if use_perimeter:
            source=self.parameterAsSource(parameters,"PERIMETER",context)
            if source is None or not source.sourceCrs().isValid():
                raise QgsProcessingException("Airway perimeter mode requires a polygon perimeter with a defined CRS.")
            mask=perimeter_geometry(source,crs,context,feedback)
        clipped=use_perimeter and self.parameterAsEnum(parameters,"CLIP",context)==0
        selection_method="perimeter" if use_perimeter else "airway_list" if airway_list else "airport_runway"

        base=re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+',"_",self.parameterAsString(parameters,"BASE_NAME",context)).strip(" ._")
        if (not base or len(base)>80 or base.lower().startswith(("gpkg_","sqlite_"))
                or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]",base.split(".")[0],re.I)):
            raise QgsProcessingException("Choose a valid base name (maximum 80 characters).")
        value=self.parameterAsFileOutput(parameters,"OUTPUT_FOLDER",context)
        if not value:
            raise QgsProcessingException("Choose an output folder.")
        output=Path(value).resolve()
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise QgsProcessingException("Output folder must be new or empty; existing data is never overwritten.")
        placed=self.parameterAsVectorLayer(parameters,"POINT_REFERENCE",context)
        records=trajectories.navigation_records(placed,1 if mode>=2 else 0,crs,context,feedback)
        grouped=False
        if mode>=4:
            rows,records=resolve_manual(self.parameterAsString(parameters,"MANUAL_POINTS",context),records,kind,tools)
            for row in rows:
                row["icao"]=airports[0]
                row["runway"]=";".join(runway_config)
                row["source_properties"].update(airport_icao=airports[0],runway_config=runway_config)
            reference_description="Manual ordered point list"
        else:
            reference=load_reference(REFERENCE_NAMES[mode],self.parameterAsVectorLayer(parameters,"REFERENCE",context),context)
            rows,grouped=reference_rows(reference,mode,crs,airports,runway_config,
                tokens(self.parameterAsString(parameters,"PROCEDURES",context)),
                airway_filters,context,feedback)
            reference_description=reference.source()
        rows=trim_rows(rows,mask,clipped,feedback)
        memory=matching_layer(rows,crs)
        routes,vertices,route_index,vertex_index=trajectories.index_routes([memory],crs,context,feedback)
        for row,route in zip(rows,routes):
            route["source"]=row["source"]
        # Only airway perimeter mode restricts anchors spatially.
        inside=lambda point: (mask is None or mask.intersects(QgsGeometry.fromPointXY(point))
                              or mask.distance(QgsGeometry.fromPointXY(point))<=0.000001)
        records=[record for record in records if inside(record["work"])]
        selected,unmatched=trajectories.match_points(routes,vertices,route_index,vertex_index,records,tolerance,False,feedback)
        unmatched=[item for item in unmatched if inside(vertices[item["vertex_id"]]["point"])]
        named_count=len(selected)
        if self.parameterAsBool(parameters,"UNNAMED",context):
            for number,item in enumerate(unmatched,1):
                vertex=vertices[item["vertex_id"]]
                selected.append({"ident":"UNNAMED_"+str(number).zfill(3),"master":"Unmatched "+kind+" vertex",
                                 "fid":item["vertex_id"],"point":vertex["geographic"],"work":vertex["point"],
                                 "routes":vertex["routes"],"vertices":{item["vertex_id"]},"distance_m":0})
        output.parent.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix=".navigation-packages-",dir=output.parent))
        try:
            shutil.copytree(QGIS_DIRECTORY/"templates"/"svg",stage/"svg")
            prefix=base+"_"+kind
            lines=write_lines(prefix+"_Lines",rows,crs,stage,output,tools,context,feedback)
            kwargs=dict(stage=stage,output=output,points=selected,fix_symbol="triangle_hollow",
                        work_crs=crs,dx=dx,dy=dy,context=context,feedback=feedback)
            symbols=tools.write_points("symbols",prefix+"_Symbols",**kwargs)
            names=tools.write_points("text",prefix+"_Names",**kwargs)
            report={"mode":kind,"manual":mode>=4,"reference":reference_description,
                    "clipped":clipped,"selection_method":selection_method,
                    "airport_icaos":airports,"runway_configuration":runway_config,
                    "airway_identifiers":airway_filters if mode<2 else [],
                    "perimeter_wgs84":None,"line_features":len(rows),"named_points":named_count,
                    "placeholder_points":len(selected)-named_count,"unmatched_vertices":unmatched,
                    "star_grouped_reference":grouped,"tolerance_metres":tolerance,
                    "name_offset_metres":{"x":dx,"y":dy},
                    "points":[{"ident":point["ident"],"source":point["master"],"fid":point["fid"],
                               "longitude":point["point"].x(),"latitude":point["point"].y(),
                               "name_category":tools.name_category(point),
                               "routes":[routes[key]["source"] for key in sorted(point["routes"])]} for point in selected],
                    "limitations":["Grouped STAR approach labels do not isolate individual STAR procedures.",
                                   "RNP filters are matched to the dataset's RNAV label spelling.",
                                   "Manual lines are straight connections through the supplied points, not computed ARINC paths.",
                                   "Only airway perimeter mode restricts point anchors; offset labels may extend outside it.",
                                   "Source geometry is drawn in 2D; aircraft performance and altitude restrictions are not calculated.",
                                   "Runway/sector activation is not inferred from procedure filters."]}
            if mask is not None:
                geographic=QgsGeometry(mask)
                geographic.transform(QgsCoordinateTransform(crs,QgsCoordinateReferenceSystem("EPSG:4326"),context.transformContext()))
                report["perimeter_wgs84"]=json.loads(geographic.asJson())
            (stage/"import_report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled; all packages discarded.")
            existed=output.exists()
            if existed:
                output.rmdir()
            try:
                os.rename(stage,output)
            except OSError:
                if existed and not output.exists():
                    output.mkdir()
                raise
        finally:
            if stage.exists():
                if stage.resolve().parent!=output.parent.resolve() or not stage.name.startswith(".navigation-packages-"):
                    raise RuntimeError("Unexpected staging directory.")
                shutil.rmtree(stage)
        if context.project() is not None:
            for key,filename in (("LINES",lines),("SYMBOLS",symbols),("NAMES",names)):
                context.addLayerToLoadOnCompletion(str(output/filename),
                    QgsProcessingContext.LayerDetails(Path(filename).stem,context.project(),key))
        feedback.pushInfo("Created {} lines, {} named points and {} placeholders; {} unmatched vertices reported.".format(
            len(rows),named_count,len(selected)-named_count,len(unmatched)))
        feedback.setProgress(100)
        return {"OUTPUT_FOLDER":str(output),"LINES":str(output/lines),"SYMBOLS":str(output/symbols),
                "NAMES":str(output/names),"REPORT":str(output/"import_report.json"),
                "LINE_COUNT":len(rows),"POINT_COUNT":len(selected),"UNMATCHED_VERTEX_COUNT":len(unmatched)}
