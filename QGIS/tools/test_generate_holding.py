"""Holding geometry and QGIS/CoFrance integration tests; run with QGIS Python."""

import gc
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest

from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
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
    import generate_holding
    TOOL = generate_holding
    package = types.ModuleType("_holding_export_check")
    package.__path__ = [str(Path(__file__).resolve().parents[2] / "QGIS-CoFrance-Exporter")]
    sys.modules[package.__name__] = package
    EXPORTER = importlib.import_module(package.__name__ + ".geojson_styler").GeoJSONStyler


def tearDownModule():
    gc.collect()
    APP.exitQgis()
    for path in DIRECTORIES:
        assert path.resolve().parent == Path(__file__).resolve().parent
        assert path.name.startswith("holding_test_")
        shutil.rmtree(path)


def signed_area(points):
    return sum(a.x() * b.y() - b.x() * a.y() for a, b in zip(points, points[1:])) / 2


class HoldingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="holding_test_", dir=Path(__file__).resolve().parent))
        DIRECTORIES.append(self.root)
        self.project = QgsProject()
        self.context = QgsProcessingContext()
        self.context.setProject(self.project)
        self.algorithm = TOOL.GenerateHolding()
        self.algorithm.initAlgorithm()
        self.feedback = QgsProcessingFeedback()
        self.params = {"HOLD_TYPE": 0, "MIN_FL": -1, "MAX_FL": -1, "IDENT": "TEST", "SOURCE": 1, "MASTER_FID": -1, "TRACK": 90,
                       "TRACK_REF": 0, "VARIATION": 0, "TURN": 0, "LEG_MIN": 1,
                       "SPEED_KT": 180, "TURN_RATE": 3, "OUTPUT": str(self.root / "Test_Hold.gpkg")}
        self.add_master("LFXX_VOR", [("TEST", 9.5, 42.5)])

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
            row = QgsFeature(layer.fields())
            row["ident"] = ident
            row.setGeometry(QgsGeometry.fromPointXY(transform.transform(QgsPointXY(lon, lat))))
            self.assertTrue(layer.dataProvider().addFeatures([row])[0])
        self.project.addMapLayer(layer)
        return layer

    def run_tool(self, **overrides):
        params = dict(self.params, **overrides)
        valid, message = self.algorithm.checkParameterValues(params, self.context)
        self.assertTrue(valid, message)
        return self.algorithm.processAlgorithm(params, self.context, self.feedback)

    def test_terminal_and_enroute_presets_and_custom_values(self):
        for hold_type, expected_time, expected_speed, expected_length in (
                (0, 1, 180, 3), (1, 1.5, 240, 6)):
            result = self.run_tool(HOLD_TYPE=hold_type, LEG_MIN=0, SPEED_KT=0,
                                   OUTPUT=str(self.root / ("Preset_" + str(hold_type) + ".gpkg")))
            layer = QgsVectorLayer(result["OUTPUT"], "preset", "ogr")
            self.project.addMapLayer(layer)
            row = next(layer.getFeatures())
            self.assertEqual(row["hold_type"], ("Terminal", "Enroute")[hold_type])
            self.assertEqual(row["leg_min"], expected_time)
            self.assertEqual(row["speed_kt"], expected_speed)
            self.assertEqual(row["leg_nm"], expected_length)
        self.assertEqual(TOOL.preset_values(1, 1.2, 210), ("Enroute", 1.2, 210))
        for args in ((2, 0, 0), (0, 0.01, 180), (1, 1, 10), (0, -1, 180)):
            with self.assertRaises(QgsProcessingException):
                TOOL.preset_values(*args)

    def test_terminal_procedure_fix_lookup_and_colocated_enroute_copy(self):
        ident = "ZZ_HOLD_PROC_TEST"
        self.add_master("LFXX_FIX_PROC", [(ident, 2, 48)])
        result = self.run_tool(IDENT=ident, HOLD_TYPE=0, SOURCE=0)
        layer = QgsVectorLayer(result["OUTPUT"], "procedure", "ogr")
        self.project.addMapLayer(layer)
        self.assertEqual(next(layer.getFeatures())["fix_source"], "LFXX_FIX_PROC")
        with self.assertRaisesRegex(QgsProcessingException, "No exact match"):
            self.run_tool(IDENT=ident, HOLD_TYPE=1, SOURCE=0,
                          OUTPUT=str(self.root / "Enroute.gpkg"))
        self.add_master("LFXX_FIX_ENR", [(ident, 2, 48)])
        stem, _, _ = TOOL.find_anchor(ident, 0, -1, self.context, self.feedback, 0)
        self.assertEqual(stem, "LFXX_FIX_PROC")
        stem, _, _ = TOOL.find_anchor(ident, 0, -1, self.context, self.feedback, 1)
        self.assertEqual(stem, "LFXX_FIX_ENR")

    def test_flight_level_range_and_cna_example_parameters(self):
        result = self.run_tool(HOLD_TYPE=1, TRACK=337, LEG_MIN=1.5, SPEED_KT=0,
                               MIN_FL=195, MAX_FL=500)
        layer = QgsVectorLayer(result["OUTPUT"], "CNA parameters", "ogr")
        self.project.addMapLayer(layer)
        row = next(layer.getFeatures())
        self.assertEqual(row["hold_type"], "Enroute")
        self.assertEqual(row["min_fl"], 195)
        self.assertEqual(row["max_fl"], 500)
        self.assertEqual(row["level_text"], "FL195–FL500")
        self.assertEqual(row["leg_min"], 1.5)
        self.assertEqual(row["input_track"], 337)
        self.assertIn("337°T", row["hold_label"])
        self.assertIn("FL195–FL500", row["hold_label"])
        self.assertEqual(TOOL.flight_levels(-1,-1), (None,None,""))
        self.assertEqual(TOOL.flight_levels(195,-1), (195,None,"FL195+"))
        with self.assertRaisesRegex(QgsProcessingException, "exceeds"):
            self.run_tool(MIN_FL=500,MAX_FL=195,
                          OUTPUT=str(self.root / "Invalid_levels.gpkg"))
        self.assertFalse((self.root / "Invalid_levels.gpkg").exists())

    def test_geometry_mirrors_closes_and_has_correct_inbound_and_scale(self):
        for track in (0, 23, 90, 180, 270, 359):
            right, length, radius = TOOL.racetrack_xy(track, True, 1.5, 240, 3)
            left, _, _ = TOOL.racetrack_xy(track, False, 1.5, 240, 3)
            self.assertEqual(right[0], right[-1])
            self.assertAlmostEqual(length, 6 * 1852)
            self.assertAlmostEqual(radius, (240 * 1852 / 3600) / math.radians(3))
            self.assertLess(signed_area(right), 0)
            self.assertGreater(signed_area(left), 0)
            angle = math.radians(track)
            u = (math.sin(angle), math.cos(angle))
            v = (math.cos(angle), -math.sin(angle))
            for r, l in zip(right, left):
                self.assertAlmostEqual(r.x()*u[0]+r.y()*u[1], l.x()*u[0]+l.y()*u[1], places=7)
                self.assertAlmostEqual(r.x()*v[0]+r.y()*v[1], -(l.x()*v[0]+l.y()*v[1]), places=7)
            inbound = (right[-1].x()-right[-2].x(), right[-1].y()-right[-2].y())
            course = math.degrees(math.atan2(*inbound)) % 360
            self.assertAlmostEqual(course, track)
            actual = sum(math.hypot(a.x()-b.x(), a.y()-b.y()) for a,b in zip(right,right[1:]))
            self.assertAlmostEqual(actual/(2*length+2*math.pi*radius), 1, places=4)

    def test_saved_layer_template_fields_style_and_exporter(self):
        result = self.run_tool(IDENT=" test ")
        path = Path(result["OUTPUT"])
        layer = QgsVectorLayer(str(path), "Test_Hold", "ogr")
        self.project.addMapLayer(layer)
        self.assertTrue(layer.isValid())
        self.assertEqual(layer.crs().authid(), "EPSG:4326")
        self.assertEqual(layer.featureCount(), 1)
        row = next(layer.getFeatures())
        self.assertEqual(row["fix_source"], "LFXX_VOR")
        self.assertEqual(row["name"], "HOLD_TERMINAL_TEST_R_090")
        self.assertEqual(row["turn_dir"], "Right")
        self.assertEqual(row["leg_nm"], 3)
        self.assertIn("activation_icao", layer.fields().names())
        self.assertIn("activation_sector_me", layer.fields().names())
        points = row.geometry().asMultiPolyline()[0]
        self.assertEqual(points[0], QgsPointXY(9.5, 42.5))
        self.assertEqual(points[-1], points[0])
        self.assertAlmostEqual(layer.renderer().symbol().symbolLayer(0).width(), 1)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("pragma quick_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("select table_name from gpkg_geometry_columns").fetchone()[0], path.stem)
        exporter = EXPORTER.__new__(EXPORTER)
        exporter.iface = None
        features = exporter.merge_to_geojson([layer])["features"]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["geometry"]["type"], "MultiLineString")
        self.assertEqual(features[0]["properties"]["name"], "HOLD_TERMINAL_TEST_R_090")
        self.assertEqual(features[0]["properties"]["lineStyle"]["width"], 1)
        self.assertTrue(self.context.willLoadLayerOnCompletion(str(path)))

    def test_left_turn_magnetic_variation_and_projected_master(self):
        self.add_master("LFXX_NDB", [("NDBX", 2, 48)], "EPSG:3857")
        result = self.run_tool(IDENT="ndbx", SOURCE=2, TURN=1, TRACK=359, TRACK_REF=1, VARIATION=3)
        layer = QgsVectorLayer(result["OUTPUT"], "left", "ogr")
        self.project.addMapLayer(layer)
        row = next(layer.getFeatures())
        self.assertEqual(row["inbound_true"], 2)
        self.assertEqual(row["track_ref"], "Magnetic")
        self.assertEqual(row["turn_dir"], "Left")
        anchor = row.geometry().asMultiPolyline()[0][0]
        self.assertAlmostEqual(anchor.x(), 2)
        self.assertAlmostEqual(anchor.y(), 48)
        projected = QgsGeometry(row.geometry())
        local = QgsCoordinateReferenceSystem.fromProj("+proj=aeqd +lat_0=48 +lon_0=2 +datum=WGS84 +units=m")
        projected.transform(QgsCoordinateTransform(layer.crs(), local, self.context.transformContext()))
        self.assertGreater(signed_area(projected.asMultiPolyline()[0]), 0)

    def test_ambiguity_requires_source_and_feature_id(self):
        self.add_master("LFXX_FIX_ENR", [("DUP", 1, 45), ("DUP", 2, 46)])
        with self.assertRaisesRegex(QgsProcessingException, "Ambiguous identifier"):
            self.run_tool(IDENT="DUP", SOURCE=3)
        self.assertFalse(Path(self.params["OUTPUT"]).exists())
        master = next(layer for layer in self.project.mapLayers().values() if layer.name()=="LFXX_FIX_ENR")
        wanted = list(master.getFeatures())[1].id()
        result = self.run_tool(IDENT="DUP", SOURCE=3, MASTER_FID=wanted)
        layer = QgsVectorLayer(result["OUTPUT"], "selected", "ogr")
        self.project.addMapLayer(layer)
        row = next(layer.getFeatures())
        self.assertEqual(row["master_fid"], wanted)
        self.assertEqual(row.geometry().asMultiPolyline()[0][0], QgsPointXY(2,46))

    def test_unknown_identifier_and_existing_output_are_protected(self):
        with self.assertRaisesRegex(QgsProcessingException, "No exact match"):
            self.run_tool(IDENT="NO_SUCH_FIX")
        output = Path(self.params["OUTPUT"])
        output.write_bytes(b"existing map")
        before = hashlib.sha256(output.read_bytes()).hexdigest()
        with self.assertRaisesRegex(QgsProcessingException, "already exists"):
            self.run_tool()
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), before)

    def test_cancellation_discards_staging_and_invalid_geometry_parameters_fail(self):
        self.feedback.cancel()
        with self.assertRaisesRegex(QgsProcessingException, "Canceled"):
            self.run_tool()
        self.assertFalse(Path(self.params["OUTPUT"]).exists())
        self.assertEqual(list(self.root.glob(".holding-*")), [])
        for values in ((0,True,0,180,3), (0,True,1,0,3), (0,True,1,180,0),
                       (float("nan"),True,1,180,3)):
            with self.assertRaises(QgsProcessingException):
                TOOL.racetrack_xy(*values)


if __name__ == "__main__":
    unittest.main()

