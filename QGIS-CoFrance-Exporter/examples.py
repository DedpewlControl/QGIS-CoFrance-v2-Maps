"""Small programmatic export example for the QGIS Python console."""

import json

from qgis.core import QgsProject, QgsVectorLayer

from .geojson_styler import GeoJSONStyler


def export_visible_vector_layers(iface, output_path):
    """Export visible vector layers using the same converter as the dialog."""
    root = QgsProject.instance().layerTreeRoot()
    layers = []
    for tree_layer in root.findLayers():
        layer = tree_layer.layer()
        if isinstance(layer, QgsVectorLayer) and tree_layer.isVisible():
            layers.append(layer)

    exporter = GeoJSONStyler(iface)
    result = exporter.merge_to_geojson(layers, {"use_layer_style": True})
    with open(output_path, "w", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, separators=(",", ":"))
    return len(result["features"])
