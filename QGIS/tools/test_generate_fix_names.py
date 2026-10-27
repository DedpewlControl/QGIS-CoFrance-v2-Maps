"""Integration tests. Run with QGIS's python-qgis-ltr.bat, not system Python."""

from contextlib import closing
import gc
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest

from qgis.core import (
    Qgis, QgsApplication, QgsFeature, QgsGeometry, QgsPointXY, QgsProcessingContext,
    QgsProcessingFeedback, QgsProject, QgsVectorFileWriter, QgsVectorLayer,
)

APP = None
GENERATOR = None
TEST_DIRECTORIES = []


def setUpModule():
    global APP, GENERATOR
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    APP = QgsApplication.instance() or QgsApplication([], False)
    APP.initQgis()
    sys.path.insert(0, str(Path(QgsApplication.prefixPath()) / "python" / "plugins"))
    import generate_fix_names
    GENERATOR = generate_fix_names


def tearDownModule():
    # OGR pools read-only source connections until QGIS shuts down on Windows.
    gc.collect()
    APP.exitQgis()
    for directory in TEST_DIRECTORIES:
        if (directory.resolve().parent != Path(__file__).resolve().parent
                or not directory.name.startswith("cofrance_names_")):
            raise RuntimeError("Unexpected test cleanup path: " + str(directory))
        shutil.rmtree(directory)


class Feedback(QgsProcessingFeedback):
    def pushWarning(self, message):
        pass

    def reportError(self, message, fatalError=False):
        pass


def point_layer(name, field, rows, symbols=False):
    uri = "Point?crs=EPSG:2154&field=" + field + ":string"
    if symbols:
        uri += "&field=symbol_type:string&field=activation_icao:string"
    layer = QgsVectorLayer(uri, name, "memory")
    for value, x in rows:
        feature = QgsFeature(layer.fields())
        feature[field] = value
        if symbols:
            feature["symbol_type"] = "triangle_hollow"
            feature["activation_icao"] = "LFQQ"
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, 6600000)))
        ok, _ = layer.dataProvider().addFeatures([feature])
        assert ok
    return layer


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FolderModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="cofrance_names_", dir=Path(__file__).resolve().parent))
        TEST_DIRECTORIES.append(self.temp)
        self.region = self.temp / "Test"
        self.symbols = self.region / "0_CCA_Test"
        self.vfr = self.region / "4_CCA_Test_VFR"
        self.project = QgsProject()
        self.context = QgsProcessingContext()
        self.context.setProject(self.project)
        for name, field, rows in (
            ("LFXX_FIX_ENR", "ident", [("ENROUTE", 600000), ("SHARED", 610000)]),
            ("LFXX_FIX_PROC", "ident", [("PROCEDURE", 620000), ("SHARED", 610000.2)]),
            ("LFXX_NDB", "ident", [("NDB", 630000)]),
            ("LFXX_VOR", "ident", [("VOR", 640000)]),
            ("LFXX_Airports", "icao", [("LFQQ", 650000)]),
        ):
            self.project.addMapLayer(point_layer(name, field, rows))
        self.write_symbols(self.symbols / "CCA_Test_FIX.gpkg", [("unused", 600000)])
        # A second table in the same package must also be scanned and deduplicated.
        self.write_symbols(self.symbols / "CCA_Test_FIX.gpkg", [("unused", 600000)], "extra")
        self.write_symbols(self.symbols / "CCA_Test_Minor_FIX.gpkg",
                           [("unused", 610000), ("unused", 620000)])
        self.write_symbols(self.symbols / "CCA_Test_NavAids.gpkg",
                           [("unused", 630000), ("unused", 640000)])
        self.write_symbols(self.vfr / "CCA_Test_ARPs.gpkg", [("unused", 650000)])
        # Unrelated VFR text must not count as ARP names.
        shutil.copyfile(GENERATOR.TEXT_TEMPLATE, self.vfr / "VFR_Points.gpkg")
        self.fix_folder = self.region / "3_CCA_Test_FIX_Names"
        self.nav_folder = self.region / "3_CCA_Test_Navaid_Names"
        self.fix_folder.mkdir()
        self.nav_folder.mkdir()
        self.expected = [self.fix_folder / "CCA_Test_FIX_Names.gpkg",
                         self.nav_folder / "CCA_Test_Navaids_Names.gpkg",
                         self.vfr / "CCA_Test_ARP_Names.gpkg"]
        self.algorithm = GENERATOR.GenerateFixNames()
        self.algorithm.initAlgorithm()
        self.parameters = {"MODE": 1, "ROOT_FOLDER": str(self.temp), "WORK_CRS": "EPSG:2154",
                           "TOLERANCE": 5, "DX": 0, "DY": 3500}

    def tearDown(self):
        self.context = None
        self.project.clear()
        self.project = None
        gc.collect()

    def write_symbols(self, path, rows, table="cofrance_symbols"):
        path.parent.mkdir(parents=True, exist_ok=True)
        layer = point_layer(path.stem, "name", rows, symbols=True)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = table
        if path.exists():
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, str(path), self.context.transformContext(), options)
        self.assertEqual(result[0], QgsVectorFileWriter.NoError, result)

    def run_folder(self):
        ok, message = self.algorithm.checkParameterValues(self.parameters, self.context)
        self.assertTrue(ok, message)
        return self.algorithm.processAlgorithm(self.parameters, self.context, Feedback())

    def test_create_repeat_and_missing_arp(self):
        self.parameters["MASTER_TYPE"] = 3  # Manual settings must not leak into folder jobs.
        before = {path: file_hash(path) for path in self.region.rglob("*.gpkg")}
        template_hash = file_hash(GENERATOR.TEXT_TEMPLATE)
        result = self.run_folder()
        self.assertEqual((result["FILE_COUNT"], result["FAILED_COUNT"], result["LABEL_COUNT"]), (3, 0, 6))
        self.assertEqual(result["DUPLICATE_COUNT"], 1)
        self.assertEqual(set(result["OUTPUTS"]), {str(path) for path in self.expected})
        for path, expected in zip(self.expected, ({"ENROUTE", "SHARED", "PROCEDURE"},
                                                  {"NDB", "VOR"}, {"LFQQ"})):
            with closing(sqlite3.connect(path)) as db:
                table = '"' + path.stem.replace('"', '""') + '"'
                self.assertEqual(db.execute("SELECT table_name FROM gpkg_geometry_columns").fetchall(), [(path.stem,)])
                self.assertEqual(db.execute("SELECT identifier FROM gpkg_contents WHERE data_type='features'").fetchall(), [(path.stem,)])
                self.assertEqual(db.execute("SELECT DISTINCT f_table_name FROM layer_styles").fetchall(), [(path.stem,)])
                self.assertEqual({row[0] for row in db.execute("SELECT text FROM " + table)}, expected)
                actual = {row[0] for row in db.execute("SELECT DISTINCT uuid FROM " + table)}
                expected = {"ARP_NAME"} if "ARP" in path.stem else {"FIX_NAME"} if "FIX" in path.stem else {"VOR_NAME", "NDB_NAME"}
                self.assertEqual(actual, expected)
                self.assertEqual(db.execute("SELECT DISTINCT activation_icao FROM " + table).fetchall(), [("LFQQ",)])
                self.assertEqual(db.execute("PRAGMA quick_check").fetchone(), ("ok",))
        self.assertEqual(before, {path: file_hash(path) for path in before})
        self.assertEqual(template_hash, file_hash(GENERATOR.TEXT_TEMPLATE))
        saved = {path: file_hash(path) for path in self.expected}
        result = self.run_folder()
        self.assertEqual((result["FILE_COUNT"], result["EXISTING_COUNT"]), (0, 3))
        self.assertEqual(saved, {path: file_hash(path) for path in self.expected})
        self.expected[2].unlink()
        result = self.run_folder()
        self.assertEqual((result["FILE_COUNT"], result["EXISTING_COUNT"]), (1, 2))
        for path in self.expected[:2]:
            self.assertEqual(file_hash(path), saved[path])

    def test_combined_tool_folder_mode_ignores_manual_settings_and_preserves_existing(self):
        self.parameters.update(MODE=1, MASTER_TYPE=3, CATEGORY="CUSTOM")
        result = self.run_folder()
        self.assertEqual((result["FILE_COUNT"], result["FAILED_COUNT"], result["LABEL_COUNT"]), (3, 0, 6))
        for path in self.expected:
            with closing(sqlite3.connect(path)) as db:
                categories = {row[0] for row in db.execute('SELECT DISTINCT uuid FROM "' + path.stem + '"')}
                expected = {"ARP_NAME"} if "ARP" in path.stem else {"FIX_NAME"} if "FIX" in path.stem else {"VOR_NAME", "NDB_NAME"}
                self.assertEqual(categories, expected)
        again = self.run_folder()
        self.assertEqual((again["FILE_COUNT"], again["EXISTING_COUNT"]), (0, 3))

    def test_existing_empty_text_is_preserved(self):
        shutil.copyfile(GENERATOR.TEXT_TEMPLATE, self.expected[0])
        before = file_hash(self.expected[0])
        result = self.run_folder()
        self.assertEqual((result["FILE_COUNT"], result["EXISTING_COUNT"]), (2, 1))
        self.assertEqual(file_hash(self.expected[0]), before)

    def test_failed_map_does_not_block_other_outputs(self):
        self.write_symbols(self.temp / "Other" / "0_CCA_Other" / "CCA_Other_FIX.gpkg", [("unused", 1)])
        result = self.run_folder()
        self.assertEqual((result["FILE_COUNT"], result["FAILED_COUNT"]), (3, 1))
        self.assertFalse((self.temp / "Other/3_CCA_Other_FIX_Names/CCA_Other_FIX_Names.gpkg").exists())

    def test_manual_automatic_categories_for_each_point_type(self):
        for group,x,expected in (("VOR",640000,"VOR_NAME"),("NDB",630000,"NDB_NAME"),
                                 ("FIX",600000,"FIX_NAME"),("ARP",650000,"ARP_NAME")):
            output=self.temp/("Auto_"+group+".gpkg")
            params=dict(self.parameters,MODE=0,CATEGORY="IFR_FIX_Names" if group=="VOR" else "",
                        OUTPUT=str(output),REFERENCE=point_layer("Manual_"+group,"name",[("unused",x)],symbols=True))
            self.algorithm.processAlgorithm(params,self.context,Feedback())
            with closing(sqlite3.connect(output)) as db:
                self.assertEqual(db.execute('SELECT uuid FROM "'+output.stem+'"').fetchall(),[(expected,)])

    def test_explicit_type_and_dme_category(self):
        reference=point_layer("Generic symbols","name",[("unused",640000)],symbols=True)
        feature=next(reference.getFeatures())
        reference.dataProvider().changeAttributeValues({feature.id():{reference.fields().indexFromName("symbol_type"):"dme"}})
        output=self.temp/"DME_Names.gpkg"
        params=dict(self.parameters,MODE=0,MASTER_TYPE=3,CATEGORY="",REFERENCE=reference,OUTPUT=str(output))
        self.algorithm.processAlgorithm(params,self.context,Feedback())
        with closing(sqlite3.connect(output)) as db:
            self.assertEqual(db.execute("SELECT uuid FROM DME_Names").fetchall(),[("DME_NAME",)])

    def test_manual_mode_still_accepts_one_layer(self):
        output = self.temp / "CCA_La_Rochelle_ARP_names.gpkg"
        params = dict(self.parameters, MODE=0, CATEGORY="Manual", OUTPUT=str(output),
                      REFERENCE=point_layer("Manual_FIX", "name", [("unused", 600000)], symbols=True))
        result = self.algorithm.processAlgorithm(params, self.context, Feedback())
        self.assertEqual((result["FILE_COUNT"], result["LABEL_COUNT"]), (1, 1))
        with closing(sqlite3.connect(output)) as db:
            self.assertEqual(db.execute("SELECT table_name FROM gpkg_geometry_columns").fetchall(), [(output.stem,)])
            self.assertEqual(db.execute("SELECT uuid FROM CCA_La_Rochelle_ARP_names").fetchall(), [("Manual",)])
            with closing(sqlite3.connect(GENERATOR.TEXT_TEMPLATE)) as template:
                self.assertEqual(db.execute("SELECT styleQML FROM layer_styles").fetchall(),
                                 template.execute("SELECT styleQML FROM layer_styles").fetchall())
        self.assertTrue(any("|layername=" + output.stem in uri
                            for uri in self.context.layersToLoadOnCompletion()))
        options = QgsVectorLayer.LayerOptions()
        options.forceReadOnly = True
        layer = QgsVectorLayer(str(output) + "|layername=" + output.stem, output.stem, "ogr", options)
        self.assertTrue(layer.isValid())
        self.assertTrue(layer.labelsEnabled())
        self.assertEqual(layer.labeling().type(), "simple")
        settings = layer.labeling().settings()
        self.assertEqual(settings.fieldName, "text")
        self.assertFalse(settings.isExpression)
        self.assertEqual(settings.format().resolvedFontFamily(), "Arial")
        self.assertEqual(settings.placement, Qgis.LabelPlacement.OverPoint)


if __name__ == "__main__":
    unittest.main()
