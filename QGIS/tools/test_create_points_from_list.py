"""Integration checks for creating CoFrance symbols and names from point lists."""

import gc
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

from qgis.PyQt.QtGui import QFontDatabase
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFeature, QgsGeometry, QgsPointXY, QgsProcessingContext,
    QgsProcessingException, QgsProcessingFeedback, QgsProject, QgsVectorLayer,
)

APP = TOOL = EXPORTER = None
DIRECTORIES = []


def setUpModule():
    global APP, TOOL, EXPORTER
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    APP = QgsApplication.instance() or QgsApplication([], False)
    APP.initQgis()
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/arial.ttf")
    import create_points_from_list
    TOOL = create_points_from_list
    package = types.ModuleType("_point_list_export_check")
    package.__path__ = [str(Path(__file__).resolve().parents[2] / "QGIS-CoFrance-Exporter")]
    sys.modules[package.__name__] = package
    EXPORTER = importlib.import_module(package.__name__ + ".geojson_styler").GeoJSONStyler


def tearDownModule():
    gc.collect()
    APP.exitQgis()
    for path in DIRECTORIES:
        assert path.resolve().parent == Path(__file__).resolve().parent
        assert path.name.startswith("points_list_test_")
        shutil.rmtree(path)


class PointListTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="points_list_test_", dir=Path(__file__).resolve().parent))
        DIRECTORIES.append(self.root)
        self.project = QgsProject()
        self.context = QgsProcessingContext()
        self.context.setProject(self.project)
        self.algorithm = TOOL.CreatePointsFromList()
        self.algorithm.initAlgorithm()
        self.feedback = QgsProcessingFeedback()
        self.params = {"POINTS": "VOR:TESTV, NDB:TESTN; FIX:TESTF", "SOURCE": 0,
                       "BASE_NAME": "CCA_Test_FIX", "FIX_SYMBOL": 0, "UNRESOLVED": 0,
                       "WORK_CRS": "EPSG:2154", "DX": 0, "DY": 3500,
                       "OUTPUT_FOLDER": str(self.root / "output")}
        self.add_master("LFXX_VOR", [("TESTV", 2, 48), ("DUP", 1, 47), ("DUP", 3, 49)])
        self.add_master("LFXX_NDB", [("TESTN", 2.5, 48.5)], "EPSG:3857")
        self.add_master("LFXX_FIX_ENR", [("TESTF", 3, 48)])
        self.add_master("LFXX_FIX_PROC", [("TESTF", 3, 48), ("TESTP", 4, 48)])

    def tearDown(self):
        self.context = None
        self.project.clear()
        self.project = None
        gc.collect()

    def add_master(self, name, rows, crs="EPSG:4326"):
        layer = QgsVectorLayer("Point?crs=" + crs + "&field=ident:string", name, "memory")
        transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                           layer.crs(), self.context.transformContext())
        for ident, lon, lat in rows:
            feature = QgsFeature(layer.fields())
            feature["ident"] = ident
            feature.setGeometry(QgsGeometry.fromPointXY(transform.transform(QgsPointXY(lon,lat))))
            self.assertTrue(layer.dataProvider().addFeatures([feature])[0])
        self.project.addMapLayer(layer)

    def run_tool(self, **changes):
        params = dict(self.params, **changes)
        valid, message = self.algorithm.checkParameterValues(params, self.context)
        self.assertTrue(valid, message)
        return self.algorithm.processAlgorithm(params, self.context, self.feedback)

    def load(self, path):
        layer = QgsVectorLayer(path, Path(path).stem, "ogr")
        self.assertTrue(layer.isValid(), path)
        self.project.addMapLayer(layer)
        return layer

    def test_mixed_sources_offsets_saved_styles_and_cofrance_export(self):
        result = self.run_tool()
        self.assertEqual(result["POINT_COUNT"], 3)
        symbols = self.load(result["SYMBOLS"])
        names = self.load(result["NAMES"])
        self.assertEqual(symbols.featureCount(), 3)
        self.assertEqual(names.featureCount(), 3)
        self.assertEqual({row["symbol_type"] for row in symbols.getFeatures()}, {"vor", "ndb", "triangle_hollow"})
        self.assertEqual({row["text"] for row in names.getFeatures()}, {"TESTV","TESTN","TESTF"})
        self.assertEqual({row["uuid"] for row in names.getFeatures()}, {"VOR_NAME", "NDB_NAME", "FIX_NAME"})
        settings = names.labeling().settings()
        self.assertTrue(names.labelsEnabled())
        self.assertEqual(settings.fieldName, "text")
        self.assertEqual(settings.placement, Qgis.LabelPlacement.OverPoint)
        self.assertEqual(settings.format().font().family(), "Arial")
        self.assertEqual(settings.format().size(), 10)
        self.assertIn("activation_icao", symbols.fields().names())
        self.assertIn("activation_sector_me", names.fields().names())
        work = QgsCoordinateReferenceSystem("EPSG:2154")
        symbol_to_work = QgsCoordinateTransform(symbols.crs(), work, self.context.transformContext())
        text_to_work = QgsCoordinateTransform(names.crs(), work, self.context.transformContext())
        point_rows = {row["ident"]: row for row in symbols.getFeatures()}
        for row in names.getFeatures():
            point = symbol_to_work.transform(point_rows[row["ident"]].geometry().asMultiPoint()[0])
            label = text_to_work.transform(row.geometry().asMultiPoint()[0])
            self.assertAlmostEqual(label.x()-point.x(), 0, places=5)
            self.assertAlmostEqual(label.y()-point.y(), 3500, places=5)
        for category in symbols.renderer().categories():
            if category.value() in ("vor","ndb"):
                path = Path(category.symbol().symbolLayer(0).path())
                self.assertTrue(path.is_file())
                self.assertTrue(path.resolve().is_relative_to(Path(self.params["OUTPUT_FOLDER"]).resolve()))
        for key in ("SYMBOLS","NAMES"):
            with sqlite3.connect(result[key]) as db:
                self.assertEqual(db.execute("pragma quick_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("select table_name from gpkg_geometry_columns").fetchone()[0],
                                 Path(result[key]).stem)
        exporter = EXPORTER.__new__(EXPORTER)
        exporter.iface = None
        features = exporter.merge_to_geojson([symbols,names])["features"]
        self.assertEqual(sum("pointStyle" in f["properties"] for f in features), 3)
        self.assertEqual(sum("textStyle" in f["properties"] for f in features), 3)
        self.assertTrue(self.context.willLoadLayerOnCompletion(result["SYMBOLS"]))
        self.assertTrue(self.context.willLoadLayerOnCompletion(result["NAMES"]))

    def test_deduplicated_list_and_colocated_fix_copies(self):
        result = self.run_tool(POINTS="testf, TESTF; PROC:TESTF\nENR:TESTF\nTESTP")
        self.assertEqual(result["POINT_COUNT"], 2)
        self.assertEqual(result["DUPLICATE_COUNT"], 3)
        report = json.loads(Path(result["REPORT"]).read_text(encoding="utf-8"))
        self.assertEqual(report["duplicate_entries"], 3)
        self.assertEqual(report["unresolved_entries"], [])

    def test_strict_mode_stops_all_outputs_and_skip_mode_reports(self):
        with self.assertRaisesRegex(QgsProcessingException, "unresolved"):
            self.run_tool(POINTS="TESTF, NO_SUCH_POINT, VOR:DUP")
        self.assertFalse(Path(self.params["OUTPUT_FOLDER"]).exists())
        result = self.run_tool(POINTS="TESTF, NO_SUCH_POINT, VOR:DUP", UNRESOLVED=1)
        self.assertEqual(result["POINT_COUNT"], 1)
        self.assertEqual(result["SKIPPED_COUNT"], 2)
        report = json.loads(Path(result["REPORT"]).read_text(encoding="utf-8"))
        self.assertEqual({entry["entry"] for entry in report["unresolved_entries"]}, {"NO_SUCH_POINT","VOR:DUP"})
        self.assertTrue(any("ambiguous" in entry["reason"] for entry in report["unresolved_entries"]))

    def test_source_feature_id_file_input_and_custom_symbol(self):
        master = next(layer for layer in self.project.mapLayers().values() if layer.name()=="LFXX_VOR")
        fid = next(row.id() for row in master.getFeatures() if row["ident"]=="DUP")
        file = self.root / "points.txt"
        file.write_text("# selected points\nVOR:DUP@" + str(fid) + "\nPROC:TESTP", encoding="utf-8-sig")
        before = file.read_bytes()
        result = self.run_tool(POINTS="", LIST_FILE=str(file), FIX_SYMBOL=1, DX=100, DY=500)
        layer = self.load(result["SYMBOLS"])
        self.assertEqual(result["POINT_COUNT"], 2)
        self.assertEqual(file.read_bytes(), before)
        rows = {row["ident"]: row for row in layer.getFeatures()}
        self.assertEqual(rows["DUP"]["source_fid"], fid)
        self.assertEqual(rows["TESTP"]["symbol_type"], "triangle_filled")

    def test_existing_output_and_cancellation_are_protected(self):
        output = Path(self.params["OUTPUT_FOLDER"])
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("existing")
        with self.assertRaisesRegex(QgsProcessingException, "new or empty"):
            self.run_tool()
        self.assertEqual(marker.read_text(), "existing")
        marker.unlink()
        self.feedback.cancel()
        with self.assertRaisesRegex(QgsProcessingException, "Canceled"):
            self.run_tool()
        self.assertEqual(list(output.iterdir()), [])
        self.assertEqual(list(self.root.glob(".points-list-*")), [])

    def test_failed_second_package_discards_both_staged_files(self):
        output = Path(self.params["OUTPUT_FOLDER"])
        output.mkdir()
        original = TOOL.configure_style
        def broken(layer,text_layer,symbols,assets):
            if text_layer:
                raise QgsProcessingException("Injected text failure")
            return original(layer,text_layer,symbols,assets)
        TOOL.configure_style = broken
        try:
            with self.assertRaisesRegex(QgsProcessingException, "Injected"):
                self.run_tool()
        finally:
            TOOL.configure_style = original
        self.assertEqual(list(output.iterdir()), [])
        self.assertEqual(list(self.root.glob(".points-list-*")), [])

    def test_invalid_list_and_crs_fail_without_outputs(self):
        for raw in ("", "VOR:", "OTHER:TEST", "DUP@1", "FIX:TEST@1"):
            with self.assertRaises(QgsProcessingException):
                TOOL.parse_points(raw)
        with self.assertRaisesRegex(QgsProcessingException, "metres"):
            self.run_tool(WORK_CRS="EPSG:4326")
        self.assertFalse(Path(self.params["OUTPUT_FOLDER"]).exists())


if __name__ == "__main__":
    unittest.main()
