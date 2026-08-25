"""QGIS to CoFrance v2 exporter plugin."""

import json
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QFileDialog, QMessageBox
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
    QgsRenderContext,
    QgsSimpleFillSymbolLayer,
    QgsSimpleLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsWkbTypes,
)

from .cofrance_schema import (
    CoFranceSchemaError,
    build_activation,
    detect_layer_kind,
    is_blank,
    round_coordinates,
    stable_json,
    validate_layer_fields,
    validate_symbol_type,
)
from .geojson_styler_dialog import GeoJSONStylerDialog


class GeoJSONStyler:
    """QGIS plugin entry point and CoFrance v2 export implementation."""

    TARGET_CRS = "EPSG:4326"

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = "&QGIS to CoFrance"
        self.toolbar = self.iface.addToolBar("QGIS to CoFrance")
        self.toolbar.setObjectName("QGIS to CoFrance")

    def add_action(
        self,
        icon_path,
        text,
        callback,
        enabled_flag=True,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None,
    ):
        """Add the plugin action to QGIS."""
        action = QAction(QIcon(icon_path), text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)
        if status_tip:
            action.setStatusTip(status_tip)
        if whats_this:
            action.setWhatsThis(whats_this)
        if add_to_toolbar:
            self.toolbar.addAction(action)
        if add_to_menu:
            self.iface.addPluginToVectorMenu(self.menu, action)
        self.actions.append(action)
        return action

    def initGui(self):
        """Create the QGIS menu and toolbar entries."""
        self.add_action(
            os.path.join(self.plugin_dir, "icon.png"),
            text="Export CoFrance v2 GeoJSON",
            callback=self.run,
            status_tip="Export selected vector layers to CoFrance v2 GeoJSON",
            parent=self.iface.mainWindow(),
        )

    def unload(self):
        """Remove the plugin UI from QGIS."""
        for action in self.actions:
            self.iface.removePluginVectorMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        del self.toolbar

    def run(self):
        """Open the export dialog."""
        self.dlg = GeoJSONStylerDialog(self.iface.mainWindow())
        self.dlg.populate_layers()
        if self.dlg.exec_():
            self.process_layers()

    def process_layers(self):
        """Validate selected layers, export them, and write the GeoJSON file."""
        selected_layers = self.dlg.get_selected_layers()
        if not selected_layers:
            QMessageBox.warning(None, "CoFrance export", "Select at least one layer.")
            return

        output_file, _ = QFileDialog.getSaveFileName(
            None,
            "Save CoFrance v2 GeoJSON",
            "",
            "GeoJSON files (*.geojson *.json)",
        )
        if not output_file:
            return
        if not output_file.lower().endswith((".geojson", ".json")):
            output_file += ".geojson"

        try:
            geojson_data = self.merge_to_geojson(
                selected_layers, self.dlg.get_style_options()
            )
            with open(output_file, "w", encoding="utf-8") as output:
                output.write('{"type":"FeatureCollection","features":[\n')
                for index, feature in enumerate(geojson_data["features"]):
                    if index:
                        output.write(",\n")
                    output.write(
                        json.dumps(
                            feature,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        )
                    )
                output.write("\n]}")
        except (CoFranceSchemaError, ValueError) as error:
            QMessageBox.critical(None, "CoFrance export validation", str(error))
            return
        except Exception as error:
            QMessageBox.critical(
                None,
                "CoFrance export",
                "The export failed unexpectedly:\n{}".format(error),
            )
            return

        QMessageBox.information(
            None,
            "CoFrance export",
            "Exported {} feature{} to:\n{}".format(
                len(geojson_data["features"]),
                "" if len(geojson_data["features"]) == 1 else "s",
                output_file,
            ),
        )

    @staticmethod
    def _sanitize_value(value):
        """Convert common QGIS/Qt values to ordinary Python values."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        try:
            if hasattr(value, "isNull") and value.isNull():
                return None
        except Exception:
            pass
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8", errors="replace")
        try:
            return value.value()
        except Exception:
            return str(value)

    def _feature_value(self, feature, field_name, default=None):
        """Read an attribute using a case-insensitive field name."""
        wanted = field_name.lower()
        for field in feature.fields():
            if field.name().lower() == wanted:
                return self._sanitize_value(feature[field.name()])
        return default

    @staticmethod
    def _geometry_family(layer):
        geometry_type = layer.geometryType()
        if geometry_type == QgsWkbTypes.PolygonGeometry:
            return "polygon"
        if geometry_type == QgsWkbTypes.LineGeometry:
            return "line"
        if geometry_type == QgsWkbTypes.PointGeometry:
            return "point"
        return "unknown"

    def _layer_kind(self, layer):
        field_names = [field.name() for field in layer.fields()]
        try:
            layer_kind = detect_layer_kind(self._geometry_family(layer), field_names)
            validate_layer_fields(layer_kind, field_names)
            return layer_kind
        except CoFranceSchemaError as error:
            raise CoFranceSchemaError(
                "Layer {!r}: {}".format(layer.name(), error)
            ) from error

    @staticmethod
    def _multi_geometry(geojson_geometry):
        """Promote a GeoJSON geometry and return appendable multi parts."""
        geometry_type = geojson_geometry.get("type")
        coordinates = geojson_geometry.get("coordinates")
        conversions = {
            "Point": ("MultiPoint", [coordinates]),
            "MultiPoint": ("MultiPoint", list(coordinates or [])),
            "LineString": ("MultiLineString", [coordinates]),
            "MultiLineString": ("MultiLineString", list(coordinates or [])),
            "Polygon": ("MultiPolygon", [coordinates]),
            "MultiPolygon": ("MultiPolygon", list(coordinates or [])),
        }
        if geometry_type not in conversions:
            raise CoFranceSchemaError(
                "Unsupported geometry type: {}".format(geometry_type)
            )
        return conversions[geometry_type]

    @staticmethod
    def _rgb(color):
        return [color.red(), color.green(), color.blue()]

    @staticmethod
    def _opacity(color, symbol):
        symbol_opacity = 1.0
        try:
            symbol_opacity = float(symbol.opacity())
        except Exception:
            pass
        return round(float(color.alphaF()) * symbol_opacity, 6)

    @staticmethod
    def _pen_dash_array(pen_style):
        dash_styles = {
            Qt.DashLine: [4.0, 2.0],
            Qt.DotLine: [1.0, 2.0],
            Qt.DashDotLine: [4.0, 2.0, 1.0, 2.0],
            Qt.DashDotDotLine: [4.0, 2.0, 1.0, 2.0, 1.0, 2.0],
        }
        return dash_styles.get(pen_style)

    def _line_style(self, symbol):
        """Extract a CoFrance lineStyle from a QGIS symbol."""
        if symbol is None:
            return {
                "color": [0, 0, 0],
                "width": 0.26,
                "opacity": 1.0,
                "dashArray": None,
            }

        simple_line = None
        for index in range(symbol.symbolLayerCount()):
            candidate = symbol.symbolLayer(index)
            if isinstance(candidate, QgsSimpleLineSymbolLayer):
                simple_line = candidate
                break

        if simple_line is not None:
            color = simple_line.color()
            width = float(simple_line.width())
            if simple_line.useCustomDashPattern():
                dash_array = [float(value) for value in simple_line.customDashVector()]
                dash_array = dash_array or None
            else:
                dash_array = self._pen_dash_array(simple_line.penStyle())
        else:
            color = symbol.color()
            width = float(symbol.width()) if hasattr(symbol, "width") else 0.26
            dash_array = None

        return {
            "color": self._rgb(color),
            "width": width,
            "opacity": self._opacity(color, symbol),
            "dashArray": dash_array,
        }

    def _polygon_styles(self, symbol):
        """Extract CoFrance fillStyle and optional outline lineStyle."""
        if symbol is None:
            return {
                "fillStyle": {"color": [255, 255, 255], "opacity": 1.0},
                "lineStyle": {
                    "color": [0, 0, 0],
                    "width": 0.26,
                    "opacity": 1.0,
                    "dashArray": None,
                },
            }

        simple_fill = None
        for index in range(symbol.symbolLayerCount()):
            candidate = symbol.symbolLayer(index)
            if isinstance(candidate, QgsSimpleFillSymbolLayer):
                simple_fill = candidate
                break
        if simple_fill is None:
            color = symbol.color()
            return {
                "fillStyle": {
                    "color": self._rgb(color),
                    "opacity": self._opacity(color, symbol),
                }
            }

        fill_color = simple_fill.color()
        styles = {
            "fillStyle": {
                "color": self._rgb(fill_color),
                "opacity": self._opacity(fill_color, symbol),
            }
        }
        if simple_fill.strokeStyle() != Qt.NoPen:
            stroke_color = simple_fill.strokeColor()
            if (
                hasattr(simple_fill, "useCustomDashPattern")
                and simple_fill.useCustomDashPattern()
            ):
                dash_array = [
                    float(value) for value in simple_fill.customDashVector()
                ] or None
            else:
                dash_array = self._pen_dash_array(simple_fill.strokeStyle())
            styles["lineStyle"] = {
                "color": self._rgb(stroke_color),
                "width": float(simple_fill.strokeWidth()),
                "opacity": self._opacity(stroke_color, symbol),
                "dashArray": dash_array,
            }
        return styles

    def _point_style(self, symbol, symbol_type):
        """Extract a CoFrance pointStyle from a QGIS marker symbol."""
        if symbol is None:
            return {
                "type": symbol_type,
                "color": [128, 128, 128],
                "size": "medium",
                "opacity": 1.0,
            }

        simple_marker = None
        for index in range(symbol.symbolLayerCount()):
            candidate = symbol.symbolLayer(index)
            if isinstance(candidate, QgsSimpleMarkerSymbolLayer):
                simple_marker = candidate
                break
        color = simple_marker.color() if simple_marker is not None else symbol.color()
        if simple_marker is not None:
            numeric_size = float(simple_marker.size())
        else:
            numeric_size = float(symbol.size()) if hasattr(symbol, "size") else 4.0
        if numeric_size < 3:
            size = "small"
        elif numeric_size < 6:
            size = "medium"
        else:
            size = "large"
        return {
            "type": symbol_type,
            "color": self._rgb(color),
            "size": size,
            "opacity": self._opacity(color, symbol),
        }

    @staticmethod
    def _css_font_weight(font):
        """Translate Qt 5/6 font weights to a useful CSS-style weight."""
        try:
            weight = int(font.weight())
        except Exception:
            return 600
        if weight > 99:
            return max(100, min(900, int(round(weight / 100.0) * 100)))
        if weight <= 25:
            return 300
        if weight <= 50:
            return 400
        if weight <= 63:
            return 500
        if weight <= 75:
            return 600
        if weight <= 87:
            return 700
        return 900

    def _text_style(self, layer, text):
        """Extract text formatting from the layer's QGIS labeling settings."""
        color = [0, 0, 0]
        font_family = "Arial"
        font_size = 12.0
        font_weight = 600
        try:
            labeling = layer.labeling()
            settings = labeling.settings() if labeling is not None else None
            text_format = settings.format() if settings is not None else None
            if text_format is not None:
                color = self._rgb(text_format.color())
                font = text_format.font()
                font_family = font.family() or font_family
                font_size = float(text_format.size())
                font_weight = self._css_font_weight(font)
        except Exception:
            # Complex rule-based labeling may not expose one reusable format.
            pass
        if font_size.is_integer():
            font_size = int(font_size)
        return {
            "color": color,
            "fontFamily": font_family,
            "fontSize": font_size,
            "fontWeight": font_weight,
            "text": str(text),
        }

    def _activation(self, feature):
        return build_activation(
            self._feature_value(feature, "activation_icao"),
            self._feature_value(feature, "activation_arr"),
            self._feature_value(feature, "activation_dep"),
        )

    def _render_context(self):
        try:
            return QgsRenderContext.fromMapSettings(
                self.iface.mapCanvas().mapSettings()
            )
        except Exception:
            return QgsRenderContext()

    def _feature_properties(self, layer, feature, layer_kind, symbol, use_layer_style):
        """Build only properties defined by the CoFrance v2 schema."""
        activation = self._activation(feature)
        if layer_kind == "text":
            uuid = self._feature_value(feature, "uuid")
            text = self._feature_value(feature, "text")
            if is_blank(uuid):
                raise CoFranceSchemaError("uuid cannot be empty.")
            if is_blank(text):
                raise CoFranceSchemaError("text cannot be empty.")
            properties = {
                "uuid": str(uuid).strip(),
                "textStyle": self._text_style(
                    layer if use_layer_style else None, text
                ),
            }
        else:
            name = self._feature_value(feature, "name")
            if is_blank(name):
                raise CoFranceSchemaError("name cannot be empty.")
            properties = {"name": str(name).strip()}
            if layer_kind == "line":
                properties["lineStyle"] = self._line_style(
                    symbol if use_layer_style else None
                )
            elif layer_kind == "polygon":
                properties.update(
                    self._polygon_styles(symbol if use_layer_style else None)
                )
            elif layer_kind == "symbol":
                symbol_type = validate_symbol_type(
                    self._feature_value(feature, "symbol_type")
                )
                properties["pointStyle"] = self._point_style(
                    symbol if use_layer_style else None, symbol_type
                )
        if activation is not None:
            properties["activation"] = activation
        return properties

    def merge_to_geojson(self, layers, style_options=None):
        """Convert selected QGIS layers into a CoFrance v2 FeatureCollection."""
        style_options = style_options or {}
        use_layer_style = style_options.get("use_layer_style", True)
        target_crs = QgsCoordinateReferenceSystem(self.TARGET_CRS)

        layer_kinds = [(layer, self._layer_kind(layer)) for layer in layers]
        grouped_features = {}
        features_in_order = []

        for layer, layer_kind in layer_kinds:
            transform = None
            if layer.crs() != target_crs:
                transform = QgsCoordinateTransform(
                    layer.crs(), target_crs, QgsProject.instance()
                )
            renderer = layer.renderer() if use_layer_style else None
            render_context = self._render_context()
            if renderer is not None:
                renderer.startRender(render_context, layer.fields())
            try:
                for feature in layer.getFeatures():
                    geometry = QgsGeometry(feature.geometry())
                    if geometry.isNull() or geometry.isEmpty():
                        continue
                    try:
                        if transform is not None:
                            geometry.transform(transform)
                        raw_geometry = json.loads(geometry.asJson())
                        raw_geometry["coordinates"] = round_coordinates(
                            raw_geometry.get("coordinates"), 6
                        )
                        geometry_type, geometry_parts = self._multi_geometry(raw_geometry)
                        symbol = (
                            renderer.symbolForFeature(feature, render_context)
                            if renderer is not None
                            else None
                        )
                        properties = self._feature_properties(
                            layer, feature, layer_kind, symbol, use_layer_style
                        )
                    except CoFranceSchemaError as error:
                        raise CoFranceSchemaError(
                            "Layer {!r}, feature {}: {}".format(
                                layer.name(), feature.id(), error
                            )
                        ) from error

                    exported = {
                        "type": "Feature",
                        "properties": properties,
                        "geometry": {
                            "type": geometry_type,
                            "coordinates": list(geometry_parts),
                        },
                    }
                    # Text rows remain distinct labels. All other geometries merge
                    # only when identifier, style, activation, and type match.
                    if layer_kind == "text":
                        features_in_order.append(exported)
                        continue
                    group_key = (geometry_type, stable_json(properties))
                    if group_key not in grouped_features:
                        grouped_features[group_key] = exported
                        features_in_order.append(exported)
                    else:
                        grouped_features[group_key]["geometry"]["coordinates"].extend(
                            geometry_parts
                        )
            finally:
                if renderer is not None:
                    renderer.stopRender(render_context)

        return {"type": "FeatureCollection", "features": features_in_order}
