"""Integration and exporter round-trip tests for the CoFrance map importer."""

from contextlib import closing
import gc
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest

from qgis.core import QgsApplication, QgsProcessingContext, QgsProcessingException, QgsProcessingFeedback, QgsProject
from qgis.PyQt.QtGui import QFontDatabase

APP = TOOL = EXPORTER = None
DIRECTORIES = []


def setUpModule():
    global APP, TOOL, EXPORTER
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    APP = QgsApplication.instance() or QgsApplication([], False)
    APP.initQgis()
    for font in ("arial.ttf", "arialbd.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
    import import_cofrance_map
    TOOL = import_cofrance_map
    plugin = Path(__file__).resolve().parents[2] / "QGIS-CoFrance-Exporter"
    package = types.ModuleType("_cofrance_import_roundtrip")
    package.__path__ = [str(plugin)]
    sys.modules[package.__name__] = package
    EXPORTER = importlib.import_module(package.__name__ + ".geojson_styler").GeoJSONStyler


def tearDownModule():
    gc.collect()
    APP.exitQgis()
    for path in DIRECTORIES:
        assert path.resolve().parent == Path(__file__).resolve().parent
        assert path.name.startswith("map_import_test_")
        shutil.rmtree(path)


def feature(properties, kind, coordinates):
    return {"type": "Feature", "properties": properties, "geometry": {"type": kind, "coordinates": coordinates}}


def normal(value):
    if isinstance(value, float):
        rounded = round(value, 4)
        return int(rounded) if rounded.is_integer() else rounded
    if isinstance(value, list):
        return [normal(v) for v in value]
    if isinstance(value, dict):
        return {k: normal(v) for k, v in value.items()}
    return value


class Feedback(QgsProcessingFeedback):
    def pushWarning(self, message):
        pass


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="map_import_test_", dir=Path(__file__).resolve().parent))
        DIRECTORIES.append(self.root)
        self.source = self.root / "exported"
        self.source.mkdir()
        self.output = self.root / "recovered"
        self.project = QgsProject()
        self.context = QgsProcessingContext()
        self.context.setProject(self.project)
        self.algorithm = TOOL.ImportCoFranceMap()
        self.algorithm.initAlgorithm()
        self.params = {"MODE": 1, "INPUT_FOLDER": str(self.source), "MAP_NAME": "Test recovered",
                       "OUTPUT_FOLDER": str(self.output)}
        polygon = feature({"name": "Background", "fillStyle": {"color": [70, 80, 90], "opacity": 0.5},
                           "lineStyle": {"color": [200, 150, 100], "width": 2, "dashArray": [4, 2]}},
                          "MultiPolygon", [[[[1, 45], [2, 45], [2, 46], [1, 46], [1, 45]],
                                            [[1.2, 45.2], [1.2, 45.4], [1.4, 45.4], [1.4, 45.2], [1.2, 45.2]]]])
        line = feature({"name": "Arrival line", "z-index": 4, "zoomin": 8, "zoomout": 15,
                        "lineStyle": {"color": [255, 189, 20], "width": 2, "dashArray": [3, 5]},
                        "activation": {"activeRunways": {"LFQQ": {"arr": ["26"], "dep": ["08"]}},
                                       "sectorOwnedByMe": ["LFFFUR+LFFFUH"], "sectorOwnedByOthers": ["LFFFUZ"]}},
                       "MultiLineString", [[[1, 45], [2, 46]], [[2, 46], [3, 47]]])
        symbols = feature({"name": "Test FIX", "pointStyle": {"type": "triangle_hollow", "color": [150, 160, 170]},
                           "activation": {"unactiveRunway": {"LFQQ": {"arr": ["26"]}}}},
                          "MultiPoint", [[2.1, 48.1], [2.2, 48.2]])
        nav = feature({"name": "Test Navaids", "pointStyle": {"type": "vor_dme", "color": [190, 190, 190]}},
                      "MultiPoint", [[2.4, 48.4]])
        self.main = [polygon, line, symbols, nav]
        self.labels = [
            feature({"uuid": "IFR_FIX_Names", "textStyle": {"text": "ONE", "fontFamily": "Arial",
                     "fontSize": 10, "fontWeight": 400, "color": [204, 204, 204]}}, "MultiPoint", [[2.1, 48.13]]),
            feature({"uuid": "IFR_FIX_Names", "textStyle": {"text": "TWO", "fontFamily": "Arial",
                     "fontSize": 12, "fontWeight": 700, "color": [255, 255, 255]}}, "MultiPoint", [[2.2, 48.23]]),
        ]
        self.arps = [
            feature({"name": "Test ARPs", "pointStyle": {"type": "aerodrome_paved", "color": [255, 255, 255]}},
                    "MultiPoint", [[2.5, 48.5]]),
            feature({"uuid": "Airport_names", "textStyle": {"text": "LFQQ", "fontFamily": "Arial",
                     "fontSize": 10, "fontWeight": 400, "color": [255, 255, 255]}}, "MultiPoint", [[2.5, 48.53]]),
        ]
        self.vfr = [feature({"uuid": "VFR Points", "textStyle": {"text": "N", "fontFamily": "Arial",
                            "fontSize": 10, "fontWeight": 400, "color": [0, 200, 255]}}, "Point", [2.6, 48.6])]
        for stem, features in (("0_CCA_Test", self.main), ("3_CCA_Test_FIX_Names", self.labels),
                                ("4_CCA_Test_ARPs", self.arps), ("4_CCA_Test_VFR_Point_Names", self.vfr)):
            self.write(stem, features)

    def tearDown(self):
        self.context = None
        self.project.clear()
        self.project = None
        gc.collect()

    def write(self, stem, features):
        path = self.source / (stem + ".geojson")
        path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
        return path

    def run_import(self, params=None):
        params = params or self.params
        ok, message = self.algorithm.checkParameterValues(params, self.context)
        self.assertTrue(ok, message)
        return self.algorithm.processAlgorithm(params, self.context, Feedback())

    def test_roundtrip_geometry_activation_styles_and_vfr_structure(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.iterdir()}
        result = self.run_import()
        self.assertEqual(result["LAYER_COUNT"], 9)
        self.assertEqual(result["FEATURE_COUNT"], 11)
        self.assertEqual(result["SKIPPED_COUNT"], 0)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})
        group = self.project.layerTreeRoot().findGroup("Test recovered")
        self.assertEqual({g.name() for g in group.children()},
                         {"0_CCA_Test", "3_CCA_Test_FIX_Names", "4_CCA_Test_VFR"})
        layers = []
        for path in result["OUTPUTS"]:
            matches = [layer for layer in self.project.mapLayers().values()
                       if layer.source().split("|")[0] == str(Path(path))]
            self.assertEqual(len(matches), 1)
            layer = matches[0]
            self.assertEqual(layer.crs().authid(), "EPSG:4326")
            self.assertIn("source_properties", layer.fields().names())
            layers.append(layer)
            if layer.name() == 'Test_Navaids':
                svg_path = Path(layer.renderer().symbol().symbolLayer(0).path())
                self.assertTrue(svg_path.is_file(), str(svg_path))
                self.assertTrue(svg_path.resolve().is_relative_to(self.output.resolve()))
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("select table_name from gpkg_geometry_columns").fetchone()[0], Path(path).stem)
                self.assertEqual(db.execute("pragma quick_check").fetchone()[0], "ok")
        exporter = EXPORTER.__new__(EXPORTER)
        exporter.iface = None
        exported = exporter.merge_to_geojson(layers)["features"]
        expected = self.main + self.labels + self.arps + self.vfr
        # The exporter promotes the single VFR point to MultiPoint.
        expected = json.loads(json.dumps(expected))
        expected[-1]["geometry"] = {"type": "MultiPoint", "coordinates": [expected[-1]["geometry"]["coordinates"]]}
        key = lambda f: json.dumps(normal(f), sort_keys=True)
        self.assertEqual(sorted(map(key, exported)), sorted(map(key, expected)))
        arp_group = group.findGroup("4_CCA_Test_VFR")
        names = [node.name() for node in arp_group.children()]
        self.assertIn("CCA_Test_ARPs", names)
        self.assertIn("CCA_Test_ARP_Names", names)
        self.assertTrue(self.project.isDirty())

    def test_single_file_and_legacy_metadata(self):
        raw = feature({"name": "Old FIX", "pointStyle": {"type": "fix_filled", "color": [100, 120, 140], "size": 8},
                       "legacy_note": "keep me"}, "Point", [2, 48])
        path = self.write("legacy", [raw])
        result = self.run_import(dict(self.params, MODE=0, INPUT=str(path)))
        self.assertEqual(result["LAYER_COUNT"], 1)
        layer = next(iter(self.project.mapLayers().values()))
        row = next(layer.getFeatures())
        self.assertEqual(row["symbol_type"], "triangle_filled")
        self.assertEqual(json.loads(row["source_properties"]), raw["properties"])
        report = json.loads(Path(result["REPORT"]).read_text(encoding="utf-8"))
        self.assertTrue(any("fix_filled" in n for n in report["notes"]))
        self.assertTrue(any("size/opacity" in n for n in report["notes"]))

    def test_legacy_uir_empty_lines_and_null_dash(self):
        solid = {"color": [73, 80, 70], "width": 0.26, "opacity": 0.5, "dashArray": None}
        raw = [
            feature({"lineStyle": solid}, "MultiLineString", []),
            feature({"lineStyle": solid}, "MultiLineString", [[[1, 45], [2, 46]], [[2, 46], [3, 47]]]),
            feature({"lineStyle": dict(solid, dashArray=[3, 3])},
                    "MultiLineString", [[[3, 47], [4, 48]]]),
            feature({"fillStyle": {"color": [65, 65, 65]}, "lineStyle": solid},
                    "Polygon", [[[1, 45], [2, 45], [2, 46], [1, 45]]]),
        ]
        path = self.write("Carte_Trajectoires_UIR", raw)
        before = path.read_bytes()
        result = self.run_import(dict(self.params, MODE=0, INPUT=str(path)))
        self.assertEqual(result["LAYER_COUNT"], 3)
        self.assertEqual(result["FEATURE_COUNT"], 4)
        self.assertEqual(result["SKIPPED_COUNT"], 1)
        self.assertEqual(path.read_bytes(), before)
        report = json.loads(Path(result["REPORT"]).read_text(encoding="utf-8"))
        self.assertEqual(report["source_features"], 4)
        self.assertEqual(report["skipped_empty_features"], 1)
        self.assertTrue(any("feature 1 has empty coordinates" in n for n in report["notes"]))
        line_layers = [layer for layer in self.project.mapLayers().values()
                       if layer.geometryType() == 1]
        self.assertEqual(len(line_layers), 2)
        solid_layer = next(layer for layer in line_layers if layer.featureCount() == 2)
        solid_symbol = solid_layer.renderer().symbol().symbolLayer(0)
        self.assertFalse(solid_symbol.useCustomDashPattern())
        self.assertAlmostEqual(solid_symbol.width(), 0.26)
        self.assertAlmostEqual(solid_symbol.color().alphaF(), 0.5, places=4)
        self.assertEqual(json.loads(next(solid_layer.getFeatures())["source_properties"])["lineStyle"], solid)
        dashed_layer = next(layer for layer in line_layers if layer.featureCount() == 1)
        dashed_symbol = dashed_layer.renderer().symbol().symbolLayer(0)
        self.assertTrue(dashed_symbol.useCustomDashPattern())
        self.assertEqual(dashed_symbol.customDashVector(), [3.0, 3.0])

    def test_xyz_trajectory_features_preserve_elevation(self):
        raw = [
            feature({"name": "Delegation", "fillStyle": {"color": [65, 65, 65]}},
                    "MultiPolygon", [[[[1, 45, 0], [2, 45, 12], [2, 46, 34], [1, 45, 0]]]]),
            feature({"name": "Trajectory", "lineStyle": {"dashArray": None}},
                    "MultiLineString", [[[1, 45, 100], [2, 46, 200]]]),
            feature({"uuid": "Reminder", "textStyle": {"text": "ROCAN"}},
                    "MultiPoint", [[2, 46, 300]]),
            feature({"name": "FIX", "pointStyle": {"type": "point"}},
                    "MultiPoint", [[3, 47, 400]]),
            feature({"name": "Trajectory", "lineStyle": {"dashArray": None}},
                    "MultiLineString", [[[1, 45], [2, 46]]]),
        ]
        path = self.write("xyz_trajectories", raw)
        result = self.run_import(dict(self.params, MODE=0, INPUT=str(path)))
        self.assertEqual(result["FEATURE_COUNT"], 5)
        self.assertEqual(result["LAYER_COUNT"], 5)
        recovered = {}
        for layer in self.project.mapLayers().values():
            for row in layer.getFeatures():
                recovered[row["source_index"]] = json.loads(row.geometry().asJson(12))
        for index, original in enumerate(raw, 1):
            self.assertEqual(recovered[index], original["geometry"])

    def test_malformed_nonempty_geometry_still_fails(self):
        raw = feature({"lineStyle": {"dashArray": None}}, "MultiLineString", ["invalid"])
        path = self.write("invalid_geometry", [raw])
        with self.assertRaises(QgsProcessingException):
            self.run_import(dict(self.params, MODE=0, INPUT=str(path)))
        self.assertFalse(self.output.exists())
        self.assertEqual(self.project.layerTreeRoot().children(), [])

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        marker = self.output / "existing.txt"
        marker.write_text("keep")
        with self.assertRaises(QgsProcessingException):
            self.run_import()
        self.assertEqual(marker.read_text(), "keep")
        self.assertEqual(self.project.layerTreeRoot().children(), [])

    def test_unsupported_activation_fails_before_writing(self):
        raw = feature({"name": "Multi airport", "pointStyle": {"type": "circle", "color": [255, 255, 255]},
                       "activation": {"activeRunways": {"LFQQ": {"arr": ["26"]}, "LFQT": {"dep": ["09"]}}}},
                      "Point", [2, 48])
        path = self.write("invalid", [raw])
        with self.assertRaisesRegex(QgsProcessingException, "one airport"):
            self.run_import(dict(self.params, MODE=0, INPUT=str(path)))
        self.assertFalse(self.output.exists())
        self.assertEqual(self.project.layerTreeRoot().children(), [])

    def test_bad_style_discards_staging_and_keeps_empty_output(self):
        self.output.mkdir()
        raw = feature({"name": "Invalid line", "lineStyle": {"width": "bad", "color": [0, 0, 0]}},
                      "LineString", [[1, 45], [2, 46]])
        path = self.write("invalid", [raw])
        with self.assertRaises(QgsProcessingException):
            self.run_import(dict(self.params, MODE=0, INPUT=str(path)))
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(list(self.root.glob(".cofrance-import-*")), [])
        self.assertEqual(self.project.layerTreeRoot().children(), [])


if __name__ == "__main__":
    unittest.main()
