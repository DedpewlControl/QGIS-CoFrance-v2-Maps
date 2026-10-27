"""QGIS integration checks for navaid perimeter/list packages."""
import json
import csv
import io
import os
import time
from pathlib import Path
import unittest
from unittest.mock import patch
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
    QgsGeometry, QgsPointXY, QgsProcessingException, QgsProcessingFeatureSourceDefinition,
    QgsVectorLayer,
)
import test_generate_navigation_packages as nav

TOOL = None


def setUpModule():
    global TOOL
    nav.setUpModule()
    import generate_navaid_packages
    TOOL = generate_navaid_packages


def tearDownModule():
    nav.tearDownModule()


class NavaidPackageTests(unittest.TestCase):
    tearDown = nav.NavigationTests.tearDown
    p = nav.NavigationTests.p
    line = nav.NavigationTests.line
    layer = nav.NavigationTests.layer
    points = nav.NavigationTests.points
    load = nav.NavigationTests.load

    def setUp(self):
        nav.NavigationTests.setUp(self)
        self.algorithm = TOOL.GenerateNavaidPackages()
        self.algorithm.initAlgorithm()
        self.params.update(SOURCES=0, TYPE_CHECK=2, TYPE_POLICY=0, SIA_AUTO_AD=False)

    def run_tool(self, **changes):
        values = dict(self.params, **changes)
        valid, message = self.algorithm.checkParameterValues(values, self.context)
        self.assertTrue(valid, message)
        return self.algorithm.processAlgorithm(values, self.context, self.feedback)

    def add_vor(self, ident, x, y):
        layer = self.project.mapLayersByName("LFXX_VOR")[0]
        feature = QgsFeature(layer.fields())
        feature["ident"] = ident
        feature.setGeometry(QgsGeometry.fromPointXY(self.p(x, y)))
        self.assertTrue(layer.dataProvider().addFeatures([feature])[0])

    def test_perimeter_symbols_names_offsets_holes_boundary_and_crs(self):
        self.add_vor("OUTSIDE", -1000, 2000)
        self.add_vor("HOLE", 5000, 5000)
        self.add_vor("BOUNDARY", 0, 2000)
        self.add_vor("CNA", 2000.2, 2000)
        original_ndb = self.project.mapLayersByName("LFXX_NDB")[0]
        original_ndb.setName("Old NDB")
        transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:2154"), QgsCoordinateReferenceSystem("EPSG:4326"), self.context.transformContext())
        geographic = transform.transform(self.p(8000, 2000))
        ndb = QgsVectorLayer("Point?crs=EPSG:4326&field=ident:string", "LFXX_NDB", "memory")
        feature = QgsFeature(ndb.fields()); feature["ident"] = "NDB1"
        feature.setGeometry(QgsGeometry.fromPointXY(geographic)); ndb.dataProvider().addFeatures([feature])
        self.project.addMapLayer(ndb)
        result = self.run_tool()
        self.assertEqual((result["POINT_COUNT"], result["DUPLICATE_COUNT"]), (3, 1))
        symbols, names = self.load(result["SYMBOLS"]), self.load(result["NAMES"])
        self.assertEqual({row["ident"] for row in symbols.getFeatures()}, {"CNA", "NDB1", "BOUNDARY"})
        self.assertEqual({row["symbol_type"] for row in symbols.getFeatures()}, {"vor", "ndb"})
        self.assertEqual({row["uuid"] for row in names.getFeatures()}, {"VOR_NAME", "NDB_NAME"})
        self.assertEqual(names.labeling().settings().fieldName, "text")
        self.assertEqual(names.labeling().settings().format().font().family(), "Arial")
        self.assertEqual(names.labeling().settings().placement, Qgis.LabelPlacement.OverPoint)
        work = QgsCoordinateReferenceSystem("EPSG:2154")
        transforms = [QgsCoordinateTransform(layer.crs(), work, self.context.transformContext()) for layer in (symbols, names)]
        anchors = {row["ident"]: transforms[0].transform(row.geometry().asMultiPoint()[0]) for row in symbols.getFeatures()}
        for row in names.getFeatures():
            text = transforms[1].transform(row.geometry().asMultiPoint()[0]); point = anchors[row["ident"]]
            self.assertAlmostEqual(text.x() - point.x(), 0, places=3)
            self.assertAlmostEqual(text.y() - point.y(), 3500, places=3)
        for path in (result["SYMBOLS"], result["NAMES"]):
            self.assertIn(Path(path).stem, Path(path).name)
        exporter = nav.EXPORTER.__new__(nav.EXPORTER)
        data = exporter.merge_to_geojson([symbols, names], {"use_layer_style": True})
        self.assertEqual(sum(len(feature["geometry"]["coordinates"]) for feature in data["features"]), 6)
        self.assertFalse(any("Lines" in path.name for path in Path(result["OUTPUT_FOLDER"]).glob("*.gpkg")))

    def test_source_filters_and_manual_list(self):
        for source, expected in ((1, {"CNA"}), (2, {"NDB1"})):
            result = self.run_tool(SOURCES=source, OUTPUT_FOLDER=str(self.root / ("source" + str(source))))
            self.assertEqual({row["ident"] for row in self.load(result["SYMBOLS"]).getFeatures()}, expected)
        result = self.run_tool(MODE=1, PERIMETER=None, IDENTIFIERS="VOR:CNA, NDB:NDB1, VOR:CNA")
        self.assertEqual((result["POINT_COUNT"], result["DUPLICATE_COUNT"]), (2, 1))
        self.assertIsNone(json.loads(Path(result["REPORT"]).read_text())["perimeter_wgs84"])
        self.assertEqual({row["uuid"] for row in self.load(result["NAMES"]).getFeatures()}, {"VOR_NAME", "NDB_NAME"})

    def test_selected_polygon_features_only(self):
        self.add_vor("OTHER", 20000, 2000)
        feature = QgsFeature(self.perimeter.fields())
        feature.setGeometry(QgsGeometry.fromPolygonXY([[self.p(19000, 0), self.p(21000, 0), self.p(21000, 4000), self.p(19000, 4000), self.p(19000, 0)]]))
        first = next(self.perimeter.getFeatures()).id()
        self.perimeter.dataProvider().addFeatures([feature])
        self.perimeter.selectByIds([first])
        source = QgsProcessingFeatureSourceDefinition(self.perimeter.id(), selectedFeaturesOnly=True)
        result = self.run_tool(PERIMETER=source)
        self.assertEqual({row["ident"] for row in self.load(result["SYMBOLS"]).getFeatures()}, {"CNA", "NDB1"})

    def test_manual_errors_and_source_restrictions_write_nothing(self):
        self.add_vor("CNA", 9000, 9000)
        for identifiers, source, message in (("VOR:CNA", 0, "ambiguous"), ("MISSING", 0, "not found"),
                                             ("FIX:FIXC", 0, "outside"), ("NDB:NDB1", 1, "outside")):
            with self.subTest(identifiers=identifiers), self.assertRaisesRegex(QgsProcessingException, message):
                self.run_tool(MODE=1, PERIMETER=None, IDENTIFIERS=identifiers, SOURCES=source)
        self.assertFalse(Path(self.params["OUTPUT_FOLDER"]).exists())
        vor = self.project.mapLayersByName("LFXX_VOR")[0]
        fid = next(vor.getFeatures()).id()
        result = self.run_tool(MODE=1, PERIMETER=None, IDENTIFIERS="VOR:CNA@" + str(fid))
        self.assertEqual(result["POINT_COUNT"], 1)

    def test_output_protection_and_atomic_second_package_failure(self):
        output = Path(self.params["OUTPUT_FOLDER"])
        output.mkdir(); marker = output / "keep.txt"; marker.write_text("keep")
        with self.assertRaisesRegex(QgsProcessingException, "new or empty"):
            self.run_tool()
        self.assertEqual(marker.read_text(), "keep")
        tools = TOOL.shared_tools()[1]
        actual_write = tools.write_points
        def fail_names(kind, *args, **kwargs):
            if kind == "text":
                raise QgsProcessingException("Injected names failure")
            return actual_write(kind, *args, **kwargs)
        with patch.object(tools, "write_points", side_effect=fail_names):
            with self.assertRaisesRegex(QgsProcessingException, "Injected"):
                self.run_tool(OUTPUT_FOLDER=str(self.root / "failed"))
        self.assertFalse((self.root / "failed").exists())
        self.assertEqual(list(self.root.glob(".navaid-packages-*")), [])

    def typed_csv(self, types):
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=["id", "ident", "type", "latitude_deg", "longitude_deg", "frequency_khz", "dme_frequency_khz"])
        writer.writeheader()
        transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:2154"), QgsCoordinateReferenceSystem("EPSG:4326"), self.context.transformContext())
        for index, (ident, kind, x, y, frequency) in enumerate(types):
            point = transform.transform(self.p(x, y))
            writer.writerow(dict(id=index + 1, ident=ident, type=kind, latitude_deg=point.y(), longitude_deg=point.x(), frequency_khz=frequency))
        path = self.root / "typed.csv"
        path.write_text(stream.getvalue(), encoding="utf-8")
        return path

    def test_external_database_corrects_dme_and_combined_ndb_dme(self):
        file = self.typed_csv([("CNA", "DME", 2000, 2000, 113800), ("NDB1", "NDB-DME", 8000, 2000, 343)])
        result = self.run_tool(TYPE_CHECK=1, TYPE_CSV=str(file))
        self.assertEqual(result["POINT_COUNT"], 2)
        symbols, names = self.load(result["SYMBOLS"]), self.load(result["NAMES"])
        self.assertEqual(symbols.featureCount(), 3)
        self.assertEqual(names.featureCount(), 2)
        self.assertEqual({(row["ident"], row["symbol_type"], row["navaid_type"]) for row in symbols.getFeatures()},
                         {("CNA", "dme", "dme"), ("NDB1", "ndb", "ndb_dme"), ("NDB1", "dme", "ndb_dme")})
        self.assertEqual({(row["text"], row["uuid"]) for row in names.getFeatures()}, {("CNA", "DME_NAME"), ("NDB1", "NDB_NAME")})
        report = json.loads(Path(result["REPORT"]).read_text())
        self.assertEqual((report["symbol_features"], report["name_features"]), (3, 2))
        self.assertEqual(report["type_database"]["mode"], "local_csv")
        self.assertTrue(all(point["type_reference_id"] for point in report["points"]))
        exporter = nav.EXPORTER.__new__(nav.EXPORTER)
        data = exporter.merge_to_geojson([symbols, names], {"use_layer_style": True})
        self.assertEqual(sum(len(feature["geometry"]["coordinates"]) for feature in data["features"]), 5)

    def test_crosscheck_frequency_distance_and_conflicts_are_not_guessed(self):
        from qgis.PyQt.QtCore import QVariant
        from qgis.core import QgsField
        vor = self.project.mapLayersByName("LFXX_VOR")[0]
        vor.dataProvider().addAttributes([QgsField("freq_mhz", QVariant.Double)]); vor.updateFields()
        fid = next(vor.getFeatures()).id()
        vor.dataProvider().changeAttributeValues({fid: {vor.fields().indexFromName("freq_mhz"): 113.8}})
        for rows, reason in (([("CNA", "DME", 2000, 2000, 115000)], "frequency mismatch"),
                             ([("CNA", "DME", 20000, 2000, 113800)], "no database match"),
                             ([("CNA", "DME", 2000, 2000, 113800), ("CNA", "VOR", 2000, 2000, 113800)], "conflicting")):
            file = self.typed_csv(rows)
            with self.assertRaisesRegex(QgsProcessingException, reason):
                self.run_tool(SOURCES=1, TYPE_CHECK=1, TYPE_CSV=str(file))
            self.assertFalse(Path(self.params["OUTPUT_FOLDER"]).exists())
        file = self.typed_csv([("CNA", "VOR-DME", 2000, 2000, 113800)])
        result = self.run_tool(TYPE_CHECK=1, TYPE_CSV=str(file), TYPE_POLICY=1)
        self.assertEqual((result["POINT_COUNT"], result["SKIPPED_COUNT"]), (1, 1))
        self.assertEqual({row["symbol_type"] for row in self.load(result["SYMBOLS"]).getFeatures()}, {"vor_dme"})

    def test_cached_online_lookup_and_download_failure_preserve_valid_cache(self):
        checker = TOOL.type_tools()
        file = self.typed_csv([("CNA", "VOR-DME", 2000, 2000, 113800), ("NDB1", "NDB", 8000, 2000, 343)])
        folder = self.root / "cache"
        with patch.object(checker, "cache_directory", return_value=folder), patch.object(checker, "fetch_database", return_value=file.read_bytes()) as fetch:
            first = self.run_tool(TYPE_CHECK=0)
            self.assertEqual(json.loads(Path(first["REPORT"]).read_text())["type_database"]["mode"], "downloaded")
            second = self.run_tool(TYPE_CHECK=0, OUTPUT_FOLDER=str(self.root / "cached"))
            self.assertEqual(json.loads(Path(second["REPORT"]).read_text())["type_database"]["mode"], "cached")
            fetch.assert_called_once()
            cached = Path(json.loads(Path(first["REPORT"]).read_text())["type_database"]["cache_file"])
            saved = cached.read_bytes()
            os.utime(cached, (time.time() - 90000, time.time() - 90000))
            fetch.side_effect = QgsProcessingException("offline")
            _, metadata = checker.load_database("", False, self.feedback)
            self.assertEqual(metadata["mode"], "cached_stale")
            with self.assertRaisesRegex(QgsProcessingException, "offline"):
                checker.load_database("", True, self.feedback)
            self.assertEqual(cached.read_bytes(), saved)
            fetch.side_effect = None; fetch.return_value = b"<html>not a database</html>"
            with self.assertRaisesRegex(QgsProcessingException, "Invalid"):
                checker.load_database("", True, self.feedback)
            self.assertEqual(cached.read_bytes(), saved)

    def test_sia_html_parses_official_enr_types_and_deleted_amendments(self):
        checker = TOOL.type_tools()
        html = b"""<html><table><tr><th>Station</th><th>Service</th><th>ID</th><th>FREQ</th><th>HOR</th><th>Coordonnees Coordinates</th></tr>
        <tr><td>ABBEVILLE</td><td><del>VOR</del>DME</td><td>ABB</td><td>108.45 MHz<br>Ch 21Y</td><td>H24</td><td>50&deg;08'06.5&quot;N 001&deg;51'16.9&quot;E</td></tr>
        <tr><td>COGNAC</td><td>VOR-DME</td><td>CNA</td><td>114.65 MHz Ch 93Y</td><td>H24</td><td>45&deg;39'34.4&quot;N 000&deg;18'41.9&quot;W</td></tr></table></html>"""
        records = checker.parse_sia_page(html, "SIA ENR 4.1")
        self.assertEqual([(row["ident"], row["type"], row["frequency_khz"]) for row in records],
                         [("ABB", "DME", 108450), ("CNA", "VOR-DME", 114650)])
        self.assertLess(records[1]["longitude_deg"], 0)
        self.assertAlmostEqual(records[0]["latitude_deg"], 50 + 8/60 + 6.5/3600)
        source = TOOL.type_tools()._sia
        cycle, urls = source.edition_sources(checker.DATABASE_URL, ["LFBD"])
        self.assertEqual(cycle, "2026-10-01")
        self.assertTrue(urls[0].endswith("FR-ENR-4.1-fr-FR.html"))
        self.assertTrue(urls[1].endswith("FR-AD-2.LFBD-fr-FR.html"))
        with patch.object(checker, "fetch_page", return_value=html):
            result = checker.parse_database(checker.fetch_database(self.feedback))
        self.assertEqual(result["ABB"][0]["type"], "dme")

    def test_sia_ad219_rowspan_and_cycle_caches_are_separate(self):
        checker = TOOL.type_tools()
        html = b"""<table><tr><th>Type (CAT ILS)</th><th>ID</th><th>FREQ</th><th>HOR</th><th>Position GEO</th></tr>
        <tr><td rowspan='2'>NDB-DME</td><td rowspan='2'>TEST</td><td>343 kHz</td><td>H24</td><td rowspan='2'>45&deg;00'00&quot;N 001&deg;00'00&quot;W</td></tr>
        <tr><td>110 MHz</td><td>H24</td></tr></table>"""
        records = checker.parse_sia_page(html, "AD2")
        self.assertEqual(len(records), 2)
        self.assertEqual({row["type"] for row in records}, {"NDB-DME"})
        self.assertEqual({row["frequency_khz"] for row in records}, {343, 110000})
        self.assertEqual(len({row["id"] for row in records}), 1)
        file = self.typed_csv([("CNA", "DME", 2000, 2000, 113800)])
        new_cycle = checker.DATABASE_URL.replace("AIRAC-2026-10-01", "AIRAC-2026-10-29").replace("eAIP_01_OCT_2026", "eAIP_29_OCT_2026")
        with patch.object(checker, "cache_directory", return_value=self.root / "cache"), patch.object(checker, "fetch_database", return_value=file.read_bytes()) as fetch:
            _, first = checker.load_database("", False, self.feedback)
            _, second = checker.load_database("", False, self.feedback, new_cycle)
            self.assertNotEqual(first["cache_file"], second["cache_file"])
            self.assertEqual((first["airac_cycle"], second["airac_cycle"]), ("2026-10-01", "2026-10-29"))
            self.assertEqual(fetch.call_count, 2)

    def test_same_database_facility_in_two_masters_gets_one_name(self):
        file = self.typed_csv([("NDB1", "NDB-DME", 8000, 2000, 343)])
        self.add_vor("NDB1", 8000, 2000)
        result = self.run_tool(TYPE_CHECK=1, TYPE_CSV=str(file), TYPE_POLICY=1)
        self.assertEqual(result["POINT_COUNT"], 1)
        self.assertGreaterEqual(result["DUPLICATE_COUNT"], 1)
        names = self.load(result["NAMES"])
        self.assertEqual(names.featureCount(), 1)
        self.assertEqual(next(names.getFeatures())["source_master"], "LFXX_NDB")
        tools = TOOL.shared_tools()[1]
        self.assertEqual(tools.name_category({"master": "LFXX_NDB", "symbol_type": "dme"}), "DME_NAME")

    def test_markdown_edition_url_is_canonical_and_uses_existing_cache(self):
        checker = TOOL.type_tools()
        url = checker.DATABASE_URL
        markdown = '[' + url + '](' + url + ')'
        self.assertEqual(checker._sia.edition_sources(markdown, ['LFBC']), checker._sia.edition_sources(url, ['LFBC']))
        file = self.typed_csv([('CNA', 'DME', 2000, 2000, 113800)])
        with patch.object(checker, 'cache_directory', return_value=self.root / 'cache'), patch.object(checker, 'fetch_database', return_value=file.read_bytes()) as fetch:
            _, first = checker.load_database('', False, self.feedback, markdown)
            _, second = checker.load_database('', False, self.feedback, url)
            self.assertEqual(first['cache_file'], second['cache_file'])
            self.assertEqual(first['source'], url)
            self.assertEqual(fetch.call_count, 1)

    def test_automatic_nearby_ad219_resolves_tacan_and_reports_old_entries(self):
        checker = TOOL.type_tools()
        base = self.typed_csv([('OTHER', 'DME', 90000, 90000, 113800)])
        empty = base.read_bytes()
        supplement = self.typed_csv([('CNA', 'TACAN', 2000, 2000, 113800)]).read_bytes()
        from qgis.core import QgsGeometry
        self.layer('LFXX_Airports', 'Point', ['icao'], [
            ({'icao': 'LFBC'}, QgsGeometry.fromPointXY(self.p(2100, 2000))),
            ({'icao': 'LFBM'}, QgsGeometry.fromPointXY(self.p(8100, 2000))),
            ({'icao': 'LFPG'}, QgsGeometry.fromPointXY(self.p(500000, 500000)))])
        # A terminal table changes CNA from its source VOR container to TACAN.
        with patch.object(checker, 'load_database', return_value=(checker.parse_database(empty), {'mode': 'cached'})), patch.object(checker, 'load_terminal_page', side_effect=[(checker.parse_database(supplement), {'airport': 'LFBC', 'mode': 'downloaded'}), ({}, {'airport': 'LFBM', 'mode': 'cached'})]) as terminal:
            result = self.run_tool(TYPE_CHECK=0, SIA_AUTO_AD=True, TYPE_POLICY=1)
        self.assertEqual({call.args[0] for call in terminal.call_args_list}, {'LFBC', 'LFBM'})
        self.assertEqual((result['POINT_COUNT'], result['SKIPPED_COUNT']), (1, 1))
        self.assertEqual({row['symbol_type'] for row in self.load(result['SYMBOLS']).getFeatures()}, {'tacan'})
        report = json.loads(Path(result['REPORT']).read_text())
        self.assertEqual(report['type_issues'][0]['ident'], 'NDB1')
        self.assertEqual(report['type_database']['automatic_ad219_airports'], ['LFBC', 'LFBM'])
        self.assertEqual(self.algorithm.parameterDefinition('TYPE_POLICY').defaultValue(), 1)
        self.assertTrue(self.algorithm.parameterDefinition('SIA_AUTO_AD').defaultValue())

    def test_terminal_cache_valid_empty_stale_failure_and_cycle_isolation(self):
        checker = TOOL.type_tools()
        html = b'<h3>AD 2.19</h3><table><tr><th>Type (CAT ILS)</th><th>ID</th><th>FREQ</th><th>Position GEO</th></tr><tr><td>TACAN</td><td>CAA</td><td>CH 45X</td><td>44&deg;31\'52.2&quot;N 001&deg;08\'09.7&quot;W</td></tr></table>'
        with patch.object(checker, 'cache_directory', return_value=self.root / 'cache'), patch.object(checker, 'fetch_page', return_value=html) as fetch:
            db, metadata = checker.load_terminal_page('LFBC', checker.DATABASE_URL, False, self.feedback)
            self.assertEqual(db['CAA'][0]['type'], 'tacan')
            self.assertEqual(db['CAA'][0]['frequencies_khz'], [])
            self.assertEqual(metadata['mode'], 'downloaded')
            _, cached = checker.load_terminal_page('LFBC', checker.DATABASE_URL, False, self.feedback)
            self.assertEqual(cached['mode'], 'cached')
            self.assertEqual(fetch.call_count, 1)
            os.utime(metadata['cache_file'], (time.time() - 90000,) * 2)
            fetch.side_effect = QgsProcessingException('offline')
            self.assertEqual(checker.load_terminal_page('LFBC', checker.DATABASE_URL, False, self.feedback)[1]['mode'], 'cached_stale')
            with self.assertRaisesRegex(QgsProcessingException, 'offline'):
                checker.load_terminal_page('LFBC', checker.DATABASE_URL, True, self.feedback)
            with self.assertRaisesRegex(QgsProcessingException, 'offline'):
                checker.load_terminal_page('LFBC', checker.DATABASE_URL.replace('AIRAC-2026-10-01', 'AIRAC-2026-10-29'), False, self.feedback)
            fetch.side_effect = None; fetch.return_value = b'<h3>AD 2.19</h3>NIL'
            self.assertEqual(checker.load_terminal_page('LFBM', checker.DATABASE_URL, False, self.feedback)[0], {})
            self.assertEqual(checker.load_terminal_page('LFBM', checker.DATABASE_URL, False, self.feedback)[1]['mode'], 'cached')
            fetch.return_value = b'<html>maintenance</html>'
            with self.assertRaisesRegex(QgsProcessingException, 'No AD 2.19'):
                checker.load_terminal_page('LFBM', checker.DATABASE_URL, True, self.feedback)

    def test_missing_perimeter_empty_selection_and_cancellation(self):
        with self.assertRaisesRegex(QgsProcessingException, "polygon perimeter"):
            self.run_tool(PERIMETER=None)
        self.project.mapLayersByName("LFXX_VOR")[0].setSubsetString("1=0")
        self.project.mapLayersByName("LFXX_NDB")[0].setSubsetString("1=0")
        with self.assertRaisesRegex(QgsProcessingException, "No navaids"):
            self.run_tool()
        self.feedback.cancel()
        with self.assertRaisesRegex(QgsProcessingException, "Canceled"):
            self.run_tool()
        self.assertFalse(Path(self.params["OUTPUT_FOLDER"]).exists())


if __name__ == "__main__":
    unittest.main()
