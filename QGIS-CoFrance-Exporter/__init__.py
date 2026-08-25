"""QGIS to CoFrance v2 plugin package."""

def classFactory(iface):
    from .geojson_styler import GeoJSONStyler
    return GeoJSONStyler(iface)
