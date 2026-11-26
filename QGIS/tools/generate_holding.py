"""QGIS Processing: draw a CoFrance holding racetrack at a named fix or navaid."""

from contextlib import closing
import gc
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
from qgis.PyQt.QtXml import QDomDocument
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDistanceArea, QgsExpression,
    QgsFeature, QgsFeatureRequest, QgsField, QgsGeometry, QgsLineSymbol, QgsPointXY,
    QgsProcessingAlgorithm, QgsProcessingContext, QgsProcessingException,
    QgsProcessingParameterDefinition, QgsProcessingParameterEnum, QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber, QgsProcessingParameterString,
    QgsSingleSymbolRenderer, QgsVectorLayer, QgsWkbTypes,
)

QGIS_DIRECTORY = Path(
    r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS"
)
SOURCES = ("LFXX_VOR", "LFXX_NDB", "LFXX_FIX_ENR", "LFXX_FIX_PROC")
# Cartographic defaults, deliberately separate from published procedure rules.
HOLD_PRESETS = (
    {"name": "Terminal", "leg_min": 1.0, "speed_kt": 180.0},
    {"name": "Enroute", "leg_min": 1.5, "speed_kt": 240.0},
)
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


def master_layer(stem, context):
    project = context.project()
    normal = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    candidates = []
    for layer in project.mapLayers().values() if project else []:
        if isinstance(layer, QgsVectorLayer) and layer.isValid() and (
                normal(layer.name()) == normal(stem)
                or normal(Path(layer.source().split("|")[0]).stem) == normal(stem)):
            candidates.append(layer)
    # A project may display the same datasource several times.
    distinct = {layer.source(): layer for layer in candidates}
    if len(distinct) > 1:
        raise QgsProcessingException("Multiple different master layers match " + stem +
                                     "; rename/remove the extra master from the project.")
    if distinct:
        layer = next(iter(distinct.values()))
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
        raise QgsProcessingException(stem + " must be a point master with a defined CRS and an ident field.")
    return layer


def preset_values(hold_type, minutes, speed):
    if hold_type not in (0, 1):
        raise QgsProcessingException("Choose Terminal or Enroute.")
    preset = HOLD_PRESETS[hold_type]
    minutes = preset["leg_min"] if minutes == 0 else minutes
    speed = preset["speed_kt"] if speed == 0 else speed
    if not math.isfinite(minutes) or not 0.1 <= minutes <= 20:
        raise QgsProcessingException("Leg time must be 0 for the preset, or 0.1 to 20 minutes.")
    if not math.isfinite(speed) or not 20 <= speed <= 600:
        raise QgsProcessingException("Drawing speed must be 0 for the preset, or 20 to 600 knots.")
    return preset["name"], minutes, speed


def flight_levels(lower, upper):
    if any(not isinstance(value, int) or not -1 <= value <= 650 for value in (lower, upper)):
        raise QgsProcessingException("Flight levels must be -1 (unspecified) or integers from 0 to 650.")
    lower = None if lower == -1 else lower
    upper = None if upper == -1 else upper
    if lower is not None and upper is not None and lower > upper:
        raise QgsProcessingException("Minimum flight level exceeds maximum flight level.")
    if lower is not None and upper is not None:
        label = "FL{}–FL{}".format(lower, upper) if lower != upper else "FL" + str(lower)
    elif lower is not None:
        label = "FL{}+".format(lower)
    elif upper is not None:
        label = "Up to FL{}".format(upper)
    else:
        label = ""
    return lower, upper, label


def find_anchor(ident, source_index, feature_id, context, feedback, hold_type=0):
    ident = ident.strip().upper()
    if not ident:
        raise QgsProcessingException("Enter the fix/navaid identifier.")
    if feature_id >= 0 and source_index == 0:
        raise QgsProcessingException("Select a specific master source when using a master feature ID.")
    # Terminal fixes may be in either fixes table. Prefer the procedure copy
    # when both tables contain the same identifier at the same location.
    automatic = (SOURCES[0], SOURCES[1], SOURCES[3], SOURCES[2]) if hold_type == 0 else SOURCES[:3]
    stems = automatic if source_index == 0 else (SOURCES[source_index - 1],)
    matches = []
    for stem in stems:
        layer = master_layer(stem, context)
        if layer is None:
            if source_index:
                raise QgsProcessingException("Master " + stem + " is unavailable. Load it or restore QGIS/navdata.")
            feedback.pushInfo("Unavailable master skipped: " + stem)
            continue
        request = QgsFeatureRequest().setFilterExpression(
            'upper(trim("ident")) = ' + QgsExpression.quotedString(ident))
        for feature in layer.getFeatures(request):
            if feature_id >= 0 and feature.id() != feature_id:
                continue
            geometry = feature.geometry()
            if geometry.isNull() or geometry.isEmpty():
                continue
            points = geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]
            if len(points) != 1:
                raise QgsProcessingException(stem + " feature " + str(feature.id()) + " must contain one point.")
            point = QgsCoordinateTransform(layer.crs(), WGS84, context.transformContext()).transform(
                QgsPointXY(points[0]))
            matches.append((stem, feature.id(), point))
    if not matches:
        raise QgsProcessingException("No exact match for " + ident + ". Check the source and identifier.")
    if source_index == 0 and len(matches) > 1:
        measure = QgsDistanceArea()
        measure.setSourceCrs(WGS84, context.transformContext())
        measure.setEllipsoid("WGS84")
        distinct = []
        for match in matches:
            equivalent = next((other for other in distinct
                               if {match[0], other[0]} == {"LFXX_FIX_ENR", "LFXX_FIX_PROC"}
                               and measure.measureLine(match[2], other[2]) <= 1), None)
            if equivalent is not None:
                feedback.pushInfo("Co-located FIX Enroute/Procedures copies: using " + equivalent[0])
            else:
                distinct.append(match)
        matches = distinct
    if len(matches) > 1:
        choices = "; ".join("{} fid={} ({:.6f}, {:.6f})".format(stem, fid, p.y(), p.x())
                            for stem, fid, p in matches)
        raise QgsProcessingException("Ambiguous identifier " + ident + ": " + choices +
                                     ". Select the source and Master feature ID shown above.")
    return matches[0]


def racetrack_xy(track_true, right, leg_minutes, speed_knots, turn_rate):
    """Local metres: fix at origin, inbound direction towards the fix."""
    values = (track_true, leg_minutes, speed_knots, turn_rate)
    if any(not math.isfinite(v) for v in values):
        raise QgsProcessingException("Holding parameters must be finite numbers.")
    if leg_minutes <= 0 or speed_knots <= 0 or turn_rate <= 0:
        raise QgsProcessingException("Leg time, speed and turn rate must be positive.")
    length = speed_knots * 1852 * leg_minutes / 60
    radius = (speed_knots * 1852 / 3600) / math.radians(turn_rate)
    track = math.radians(track_true % 360)
    along = (math.sin(track), math.cos(track))
    side = (math.cos(track), -math.sin(track))
    sign = 1 if right else -1
    def point(u, v):
        return QgsPointXY(u * along[0] + sign * v * side[0],
                          u * along[1] + sign * v * side[1])
    points = [point(0, 0)]
    # At the fix: turn from inbound to outbound, then fly the timed leg.
    for step in range(1, 61):
        angle = math.pi * step / 60
        points.append(point(radius * math.sin(angle), radius * (1 - math.cos(angle))))
    points[-1] = point(0, 2 * radius)
    segments = max(1, math.ceil(length / 500))
    for step in range(1, segments + 1):
        points.append(point(-length * step / segments, 2 * radius))
    for step in range(1, 61):
        angle = math.pi * step / 60
        points.append(point(-length - radius * math.sin(angle), radius * (1 + math.cos(angle))))
    points[-1] = point(-length, 0)
    for step in range(1, segments + 1):
        points.append(point(-length * (1 - step / segments), 0))
    points[-1] = points[0]
    return points, length, radius


def holding_geometry(anchor, track, right, minutes, speed, rate, context):
    if not (-180 <= anchor.x() <= 180 and -89 < anchor.y() < 89):
        raise QgsProcessingException("The anchor is outside supported longitude/latitude bounds.")
    points, length, radius = racetrack_xy(track, right, minutes, speed, rate)
    local = QgsCoordinateReferenceSystem.fromProj(
        "+proj=aeqd +lat_0={:.12f} +lon_0={:.12f} +datum=WGS84 +units=m +no_defs".format(
            anchor.y(), anchor.x()))
    if not local.isValid():
        raise QgsProcessingException("Cannot construct the local holding CRS.")
    transform = QgsCoordinateTransform(local, WGS84, context.transformContext())
    geographic = [transform.transform(point) for point in points]
    # Preserve the precise master coordinate at the inbound endpoint.
    geographic[0] = geographic[-1] = QgsPointXY(anchor)
    # A longitude wrap would render as a world-spanning line in some consumers.
    if any(abs(a.x() - b.x()) > 180 for a, b in zip(geographic, geographic[1:])):
        raise QgsProcessingException("This hold crosses the antimeridian; move/split it manually.")
    return QgsGeometry.fromMultiPolylineXY([geographic]), length, radius


def write_package(output, name, geometry, attributes, feedback):
    template = QGIS_DIRECTORY / "templates" / "cofrance_lines.gpkg"
    if not template.is_file():
        raise QgsProcessingException("Missing template: " + str(template))
    if output.exists():
        raise QgsProcessingException("Output already exists; choose a new GeoPackage.")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".holding-", dir=output.parent))
    package = stage / output.name
    layer = None
    try:
        with closing(sqlite3.connect(template.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            original = source.execute("SELECT table_name FROM gpkg_geometry_columns").fetchone()[0]
            quoted = '"' + original.replace('"', '""') + '"'
            if source.execute("SELECT count(*) FROM " + quoted).fetchone()[0]:
                raise QgsProcessingException("The holding line template must be empty.")
            with closing(sqlite3.connect(package)) as target:
                source.backup(target)
        dataset = None
        try:
            with gdal.ExceptionMgr():
                dataset = gdal.OpenEx(str(package), gdal.OF_VECTOR | gdal.OF_UPDATE)
                if original != name:
                    dataset.ExecuteSQL("ALTER TABLE " + quoted + ' RENAME TO "' + name.replace('"', '""') + '"')
        finally:
            if dataset is not None:
                dataset.Close()
        with closing(sqlite3.connect(package)) as db:
            db.execute("UPDATE gpkg_contents SET identifier=? WHERE table_name=?", (name, name))
            db.execute("UPDATE layer_styles SET f_table_name=? WHERE f_table_name=?", (name, original))
            db.commit()
        try:
            layer = QgsVectorLayer(str(package) + "|layername=" + name, name, "ogr")
            if not layer.isValid() or not layer.startEditing():
                raise QgsProcessingException("Cannot edit the holding output.")
            strings = {"fix_ident", "fix_source", "turn_dir", "track_ref", "model", "hold_type",
                       "level_text", "hold_label"}
            for field in attributes:
                if layer.fields().indexFromName(field) < 0:
                    kind = (QVariant.String if field in strings else QVariant.LongLong if field == "master_fid"
                            else QVariant.Int if field in ("min_fl", "max_fl") else QVariant.Double)
                    if not layer.addAttribute(QgsField(field, kind)):
                        raise QgsProcessingException("Cannot add holding field " + field)
            feature = QgsFeature(layer.fields())
            feature.setGeometry(geometry)
            for field, value in attributes.items():
                feature[field] = value
            if not layer.addFeature(feature) or not layer.commitChanges():
                raise QgsProcessingException("Cannot save holding: " + "; ".join(layer.commitErrors()))
            symbol = QgsLineSymbol.createSimple({"line_color": "160,160,160", "line_width": "1"})
            symbol.symbolLayer(0).setWidthUnit(Qgis.RenderUnit.Pixels)
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            layer.setLabelsEnabled(False)
            document = QDomDocument()
            error = layer.exportNamedStyle(document)
            if error:
                raise QgsProcessingException(error)
            qml = document.toString()
        finally:
            if layer is not None:
                if layer.isEditable():
                    layer.rollBack()
                sip.delete(layer)
                layer = None
            gc.collect()
        with closing(sqlite3.connect(package)) as db:
            updated = db.execute("UPDATE layer_styles SET styleQML=?, useAsDefault=1 WHERE f_table_name=?",
                                 (qml, name)).rowcount
            if not updated:
                raise QgsProcessingException("Template has no saved style.")
            db.commit()
        if feedback.isCanceled():
            raise QgsProcessingException("Canceled; holding output discarded.")
        # Windows rename refuses an existing destination, including a race.
        os.rename(package, output)
    finally:
        if stage.resolve().parent != output.parent.resolve() or not stage.name.startswith(".holding-"):
            raise RuntimeError("Unexpected holding staging path.")
        shutil.rmtree(stage)


class GenerateHolding(QgsProcessingAlgorithm):
    def name(self):
        return "generate_cofrance_holding"

    def displayName(self):
        return "Generate CoFrance holding pattern"

    def group(self):
        return "CoFrance"

    def groupId(self):
        return "cofrance"

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def createInstance(self):
        return GenerateHolding()

    def shortHelpString(self):
        return (
            "Choose Terminal or Enroute and draw an editable holding racetrack at a named VOR, NDB or FIX. "
            "Terminal drawing defaults: 1 minute at 180 kt; Enroute: 1.5 minutes at 240 kt. "
            "These are editable drawing presets, not published holding rules. Enter 0 for time/speed to use the preset. "
            "Terminal automatic lookup includes FIX Procedures and FIX Enroute; Enroute uses FIX Enroute. "
            "Track is INBOUND towards the fix, clockwise from north. Right/left describes "
            "the turn direction viewed along the inbound track. True is the default; "
            "for magnetic tracks enter variation (east positive, west negative). "
            "Flight levels are stored as attributes, without changing the drawing size. "
            "Speed converts straight-leg minutes into distance; turns default to 3 degrees/second. This is a still-air map drawing model, "
            "not a protected holding-area or flight-performance calculation. "
            "Loaded LFXX masters take precedence over QGIS/navdata. Ambiguous identifiers "
            "require a source and master feature ID. Existing outputs are refused. "
            "The result uses the CoFrance line template and loads with a saved line style."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum("HOLD_TYPE", "Holding type / drawing preset",
                          options=["Terminal: 1 min at 180 kt", "Enroute: 1.5 min at 240 kt"], defaultValue=0))
        self.addParameter(QgsProcessingParameterString("IDENT", "Fix / navaid identifier"))
        self.addParameter(QgsProcessingParameterEnum("SOURCE", "Master source",
                          options=["Automatic: based on holding type", *SOURCES], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber("MASTER_FID", "Master feature ID (-1 = automatic)",
                          QgsProcessingParameterNumber.Integer, defaultValue=-1, minValue=-1))
        self.addParameter(QgsProcessingParameterNumber("TRACK", "Inbound holding track (degrees)",
                          QgsProcessingParameterNumber.Double, defaultValue=0, minValue=0, maxValue=360))
        self.addParameter(QgsProcessingParameterEnum("TRACK_REF", "Track reference",
                          options=["True north", "Magnetic: enter variation below"], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber("VARIATION", "Magnetic variation (degrees: east + / west -)",
                          QgsProcessingParameterNumber.Double, defaultValue=0, minValue=-180, maxValue=180))
        self.addParameter(QgsProcessingParameterEnum("TURN", "Holding turns",
                          options=["Right", "Left"], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber("LEG_MIN", "Straight-leg time (minutes; 0 = type preset)",
                          QgsProcessingParameterNumber.Double, defaultValue=0, minValue=0, maxValue=20))
        self.addParameter(QgsProcessingParameterNumber("SPEED_KT", "Drawing speed (knots; 0 = type preset)",
                          QgsProcessingParameterNumber.Double, defaultValue=0, minValue=0, maxValue=600))
        self.addParameter(QgsProcessingParameterNumber("MIN_FL", "Minimum flight level (-1 = unspecified)",
                          QgsProcessingParameterNumber.Integer, defaultValue=-1, minValue=-1, maxValue=650))
        self.addParameter(QgsProcessingParameterNumber("MAX_FL", "Maximum flight level (-1 = unspecified)",
                          QgsProcessingParameterNumber.Integer, defaultValue=-1, minValue=-1, maxValue=650))
        self.addParameter(QgsProcessingParameterNumber("TURN_RATE", "Drawing turn rate (degrees / second)",
                          QgsProcessingParameterNumber.Double, defaultValue=3, minValue=0.1, maxValue=6))
        self.addParameter(QgsProcessingParameterFileDestination("OUTPUT", "Holding GeoPackage",
                          fileFilter="GeoPackage (*.gpkg)"))
        for key in ("MASTER_FID", "TURN_RATE"):
            parameter = self.parameterDefinition(key)
            parameter.setFlags(parameter.flags() | QgsProcessingParameterDefinition.FlagAdvanced)

    def processAlgorithm(self, parameters, context, feedback):
        ident = self.parameterAsString(parameters, "IDENT", context).strip().upper()
        hold_type = self.parameterAsEnum(parameters, "HOLD_TYPE", context)
        kind, minutes, speed = preset_values(
            hold_type, self.parameterAsDouble(parameters, "LEG_MIN", context),
            self.parameterAsDouble(parameters, "SPEED_KT", context))
        lower, upper, level_text = flight_levels(
            self.parameterAsInt(parameters, "MIN_FL", context),
            self.parameterAsInt(parameters, "MAX_FL", context))
        stem, fid, anchor = find_anchor(ident, self.parameterAsEnum(parameters, "SOURCE", context),
                                       self.parameterAsInt(parameters, "MASTER_FID", context),
                                       context, feedback, hold_type)
        entered = self.parameterAsDouble(parameters, "TRACK", context)
        magnetic = self.parameterAsEnum(parameters, "TRACK_REF", context) == 1
        variation = self.parameterAsDouble(parameters, "VARIATION", context) if magnetic else 0
        track = (entered + variation) % 360
        right = self.parameterAsEnum(parameters, "TURN", context) == 0
        rate = self.parameterAsDouble(parameters, "TURN_RATE", context)
        geometry, length, radius = holding_geometry(anchor, track, right, minutes, speed, rate, context)
        value = self.parameterAsFileOutput(parameters, "OUTPUT", context)
        if not value or Path(value).suffix.lower() != ".gpkg":
            raise QgsProcessingException("Choose a new .gpkg output.")
        output = Path(value).resolve()
        name = output.stem
        if not name or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.lower().startswith(("gpkg_", "sqlite_")):
            raise QgsProcessingException("Choose a GeoPackage filename suitable for a layer name.")
        attributes = {
            "name": "HOLD_{}_{}_{}_{:03.0f}".format(kind.upper(), ident, "R" if right else "L", entered % 360),
            "hold_type": kind,
            "fix_ident": ident, "fix_source": stem, "master_fid": fid,
            "input_track": entered % 360, "track_ref": "Magnetic" if magnetic else "True",
            "mag_var": variation, "inbound_true": track, "turn_dir": "Right" if right else "Left",
            "leg_min": minutes, "speed_kt": speed, "turn_dps": rate,
            "leg_nm": length / 1852, "radius_nm": radius / 1852,
            "min_fl": lower, "max_fl": upper, "level_text": level_text,
            "hold_label": "{} | {:03.0f}°{} | {} | {:g} min".format(
                ident, entered % 360, "M" if magnetic else "T", "R" if right else "L", minutes)
                + (" | " + level_text if level_text else ""),
            "model": "Still-air racetrack for map drawing",
        }
        feedback.pushInfo("{} drawing: {:.2f} minute legs at {:.1f} kt.".format(kind, minutes, speed))
        if level_text:
            feedback.pushInfo("Altitude range: " + level_text + " (stored as attributes).")
        feedback.pushInfo("Anchor: {} fid={} at {:.8f}, {:.8f}".format(stem, fid, anchor.y(), anchor.x()))
        feedback.pushInfo("Inbound {:.2f} true; {} turns; {:.3f} NM legs; {:.3f} NM turn radius.".format(
            track, attributes["turn_dir"].lower(), length / 1852, radius / 1852))
        if magnetic and variation == 0:
            feedback.pushWarning("Magnetic variation is zero: the input track is being treated as true.")
        write_package(output, name, geometry, attributes, feedback)
        if context.project() is not None:
            context.addLayerToLoadOnCompletion(str(output),
                QgsProcessingContext.LayerDetails(name, context.project(), "OUTPUT"))
        feedback.setProgress(100)
        return {"OUTPUT": str(output)}
