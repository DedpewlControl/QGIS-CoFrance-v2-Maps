"""QGIS checks for separate airport Symbols/Names and runway Lines tools."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProcessingException
import test_generate_navigation_packages as nav

AIRPORT = RUNWAY = None


def setUpModule():
    global AIRPORT, RUNWAY
    nav.setUpModule()
    import generate_airport_packages, generate_runway_packages
    AIRPORT, RUNWAY = generate_airport_packages, generate_runway_packages


def tearDownModule():
    nav.tearDownModule()


class ReferencePackageTests(unittest.TestCase):
    tearDown = nav.NavigationTests.tearDown
    p = nav.NavigationTests.p
    line = nav.NavigationTests.line
    layer = nav.NavigationTests.layer
    points = nav.NavigationTests.points
    load = nav.NavigationTests.load

    def setUp(self):
        nav.NavigationTests.setUp(self)
        from qgis.core import QgsGeometry
        self.airports = self.layer("LFXX_Airports", "Point", ["icao"], [
            ({"icao": code}, QgsGeometry.fromPointXY(self.p(x, y)))
            for code, x, y in (("LFBD", 2000, 2000), ("LFBO", 8000, 2000), ("LFXX", 5000, 5000), ("LFBH", -1000, 2000))])
        self.runways = self.layer("LFXX_Runways", "LineString", ["icao", "rwy1", "rwy2"], [
            ({"icao": "LFBD", "rwy1": "05", "rwy2": "23"}, self.line([(-1000, 2000), (3000, 2000)])),
            ({"icao": "LFBO", "rwy1": "14L", "rwy2": "32R"}, self.line([(2000, 8000), (8000, 8000)])),
            ({"icao": "LFXX", "rwy1": "09", "rwy2": "27"}, self.line([(4500, 5000), (5500, 5000)]))])
        self.params.update(ICAOS="", SYMBOL_MODE=1, SYMBOL=0, RUNWAYS="", CLIP=0)

    def run_tool(self, airport=True, **changes):
        algorithm = AIRPORT.GenerateAirportPackages() if airport else RUNWAY.GenerateRunwayPackages()
        algorithm.initAlgorithm()
        values = dict(self.params, **changes)
        valid, message = algorithm.checkParameterValues(values, self.context)
        self.assertTrue(valid, message)
        return algorithm.processAlgorithm(values, self.context, self.feedback)

    @staticmethod
    def runway_html(rows):
        header = '<table><tr><th>RWY NR</th><th>True and Mag Bearing</th><th>Dimensions of RWY (M)</th><th>Strength (PCN) and surface of RWY and SWY</th></tr>'
        return (header + ''.join('<tr><td>{}</td><td>045</td><td>{} x 45</td><td>{}</td></tr>'.format(*row) for row in rows) + '</table>').encode('utf-8')

    def test_automatic_airport_surface_packages_and_report(self):
        folder = self.root / 'html'; folder.mkdir()
        for code, rows in {
            'LFBD': [('05', 3100, '64 F/C/W/T enrobé bitumineux / bituminous mix'), ('23', 3100, 'bituminous mix')],
            'LFBO': [('14L', 800, 'béton / concrete'), ('32R', 800, 'herbe / grass')],
            'LFBH': [('09', 1000, 'non revêtu / unpaved grass')],
        }.items():
            (folder / ('FR-AD-2.' + code + '-fr-FR.html')).write_bytes(self.runway_html(rows))
        result = self.run_tool(MODE=1, ICAOS='LFBD LFBO LFBH', SYMBOL_MODE=0, SIA_HTML_FOLDER=str(folder))
        symbols = self.load(result['SYMBOLS'])
        self.assertEqual({row['ident']: row['symbol_type'] for row in symbols.getFeatures()},
            {'LFBD': 'aerodrome_paved_ticks', 'LFBO': 'aerodrome_paved_ticks', 'LFBH': 'aerodrome_ticks'})
        self.assertEqual({row['uuid'] for row in self.load(result['NAMES']).getFeatures()}, {'ARP_NAME'})
        self.assertEqual({row['ident']: row['runway_length_m'] for row in symbols.getFeatures()}, {'LFBD': 3100, 'LFBO': 800, 'LFBH': 1000})
        report = json.loads(Path(result['REPORT']).read_text())
        self.assertEqual(report['airport_symbol_selection'], 'sia_ad_2_12')
        self.assertEqual(len(report['airport_runway_data']), 3)
        self.assertTrue(all(item['airac_cycle'] == '2026-10-01' for item in report['airport_runway_data']))
        exporter = nav.EXPORTER.__new__(nav.EXPORTER)
        data = exporter.merge_to_geojson([symbols, self.load(result['NAMES'])], {'use_layer_style': True})
        self.assertEqual(sum(len(row['geometry']['coordinates']) for row in data['features']), 6)

    def test_automatic_missing_or_unknown_surface_writes_nothing(self):
        folder = self.root / 'html'; folder.mkdir()
        with self.assertRaisesRegex(QgsProcessingException, 'Cannot read saved'):
            self.run_tool(MODE=1, ICAOS='LFBD', SYMBOL_MODE=0, SIA_HTML_FOLDER=str(folder))
        (folder / 'FR-AD-2.LFBD-fr-FR.html').write_bytes(self.runway_html([('05', 3100, 'Unknown')]))
        with self.assertRaisesRegex(QgsProcessingException, 'Unrecognized runway surface'):
            self.run_tool(MODE=1, ICAOS='LFBD', SYMBOL_MODE=0, SIA_HTML_FOLDER=str(folder))
        self.assertFalse(Path(self.params['OUTPUT_FOLDER']).exists())
        self.assertFalse(list(self.root.glob('.reference-packages-*')))

    def test_surface_parser_and_cache_are_edition_specific(self):
        reference = AIRPORT._shared.airport_reference()
        fixture = self.runway_html([('05', 3100, '<del>grass</del>béton / concrete'), ('23', 3100, 'concrete')])
        self.assertEqual(reference.classification(reference.parse_runways(fixture)), 'aerodrome_paved_ticks')
        self.assertEqual(reference.surface_type('non revêtu / unpaved'), 'unpaved')
        self.assertIsNone(reference.surface_type('grass and concrete'))
        # A runway column outside the physical-characteristics table is ignored.
        with self.assertRaisesRegex(QgsProcessingException, 'AD 2.12'):
            reference.parse_runways(b'<table><tr><th>RWY NR</th><th>Surface of apron</th></tr><tr><td>05</td><td>concrete</td></tr></table>')
        with patch.object(reference.network, 'cache_directory', return_value=self.root / 'cache'), patch.object(reference.network, 'fetch_page', return_value=fixture) as fetch:
            first = reference.load_airport('LFBD', reference.DEFAULT_INDEX, False, self.feedback)
            second = reference.load_airport('LFBD', reference.DEFAULT_INDEX, False, self.feedback)
            self.assertEqual((first['mode'], second['mode'], fetch.call_count), ('downloaded', 'cached', 1))
            import os, time
            for cache in (self.root / 'cache').glob('*.json'):
                os.utime(cache, (time.time() - 90000,) * 2)
            fetch.side_effect = QgsProcessingException('network unavailable')
            self.assertEqual(reference.load_airport('LFBD', reference.DEFAULT_INDEX, False, self.feedback)['mode'], 'cached_stale')
            with self.assertRaisesRegex(QgsProcessingException, 'network unavailable'):
                reference.load_airport('LFBD', reference.DEFAULT_INDEX, True, self.feedback)
            with self.assertRaisesRegex(QgsProcessingException, 'network unavailable'):
                reference.load_airport('LFBD', reference.DEFAULT_INDEX.replace('2026-10-01', '2026-10-29'), False, self.feedback)

    def test_airport_perimeter_symbols_names_and_export(self):
        result = self.run_tool(SYMBOL=1)
        self.assertEqual(result["POINT_COUNT"], 2)
        symbols, names = self.load(result["SYMBOLS"]), self.load(result["NAMES"])
        self.assertEqual({row["ident"] for row in symbols.getFeatures()}, {"LFBD", "LFBO"})
        self.assertEqual({row["symbol_type"] for row in symbols.getFeatures()}, {"aerodrome_paved"})
        self.assertEqual({row["uuid"] for row in names.getFeatures()}, {"ARP_NAME"})
        self.assertEqual({row["text"] for row in names.getFeatures()}, {"LFBD", "LFBO"})
        self.assertEqual(names.labeling().settings().fieldName, "text")
        work = QgsCoordinateReferenceSystem("EPSG:2154")
        transforms = [QgsCoordinateTransform(layer.crs(), work, self.context.transformContext()) for layer in (symbols, names)]
        anchors = {row["ident"]: transforms[0].transform(row.geometry().asMultiPoint()[0]) for row in symbols.getFeatures()}
        for row in names.getFeatures():
            point = transforms[1].transform(row.geometry().asMultiPoint()[0])
            self.assertAlmostEqual(point.y() - anchors[row["ident"]].y(), 3500, places=3)
        exporter = nav.EXPORTER.__new__(nav.EXPORTER)
        data = exporter.merge_to_geojson([symbols, names], {"use_layer_style": True})
        self.assertEqual(sum(len(feature["geometry"]["coordinates"]) for feature in data["features"]), 4)
        self.assertEqual(len(list(Path(result["OUTPUT_FOLDER"]).glob("*.gpkg"))), 2)
        self.assertNotIn("LINES", result)
        from importlib import import_module
        planner = import_module(nav.EXPORTER.__module__.rsplit(".", 1)[0] + ".export_plan")
        batches = planner.plan_group_exports([(symbols, ("4_CCA_Test_VFR",)), (names, ("4_CCA_Test_VFR",))])
        self.assertEqual([(name, len(layers)) for name, layers in batches], [("4_CCA_Test_ARPs.geojson", 2)])

    def test_airport_manual_list_ignores_perimeter_and_has_no_runway_outputs(self):
        result = self.run_tool(MODE=1, PERIMETER=None, ICAOS="LFXX, LFBH", DX=120, DY=900)
        self.assertEqual(result["POINT_COUNT"], 2)
        self.assertEqual({row["text"] for row in self.load(result["NAMES"]).getFeatures()}, {"LFXX", "LFBH"})
        report = json.loads(Path(result["REPORT"]).read_text())
        self.assertIsNone(report["perimeter_wgs84"])
        self.assertEqual(report["name_offset_metres"], {"x": 120, "y": 900})

    def test_runway_perimeter_keeps_full_lines_or_clips_on_request(self):
        for clip in (0, 1):
            result = self.run_tool(airport=False, CLIP=clip, OUTPUT_FOLDER=str(self.root / str(clip)))
            self.assertEqual(result["LINE_COUNT"], 2)
            self.assertNotIn("SYMBOLS", result); self.assertNotIn("NAMES", result)
            self.assertEqual(len(list(Path(result["OUTPUT_FOLDER"]).glob("*.gpkg"))), 1)
            lines = self.load(result["LINES"])
            transform = QgsCoordinateTransform(lines.crs(), QgsCoordinateReferenceSystem("EPSG:2154"), self.context.transformContext())
            row = next(feature for feature in lines.getFeatures() if feature["icao"] == "LFBD")
            geometry = row.geometry(); geometry.transform(transform)
            self.assertAlmostEqual(geometry.boundingBox().xMinimum(), 700000 if clip else 699000, places=3)
            exporter = nav.EXPORTER.__new__(nav.EXPORTER)
            self.assertTrue(exporter.merge_to_geojson([lines], {"use_layer_style": True})["features"])

    def test_runway_manual_icao_and_either_end_filter(self):
        for end in ("5", "23"):
            result = self.run_tool(airport=False, MODE=1, PERIMETER=None, ICAOS="lfbd", RUNWAYS=end,
                                   OUTPUT_FOLDER=str(self.root / end))
            self.assertEqual(result["LINE_COUNT"], 1)
            row = next(self.load(result["LINES"]).getFeatures())
            self.assertEqual(row["icao"], "LFBD")
            self.assertEqual(row["runway"], "05/23")
            properties = json.loads(row["source_properties"])
            self.assertEqual((properties["rwy1"], properties["rwy2"]), ("05", "23"))

    def test_missing_icao_bad_runway_and_existing_output_are_rejected(self):
        for airport in (True, False):
            with self.assertRaisesRegex(QgsProcessingException, "No matching"):
                self.run_tool(airport=airport, MODE=1, PERIMETER=None, ICAOS="ZZZZ")
        with self.assertRaisesRegex(QgsProcessingException, "runway ends"):
            self.run_tool(airport=False, RUNWAYS="37")
        output = Path(self.params["OUTPUT_FOLDER"]); output.mkdir()
        marker = output / "keep.txt"; marker.write_text("keep")
        with self.assertRaisesRegex(QgsProcessingException, "new or empty"):
            self.run_tool()
        self.assertEqual(marker.read_text(), "keep")

    def test_airport_second_package_failure_discards_both(self):
        tools = AIRPORT._shared.shared_tools()[1]
        actual = tools.write_points
        def fail_names(kind, *args, **kwargs):
            if kind == "text": raise QgsProcessingException("Injected failure")
            return actual(kind, *args, **kwargs)
        with patch.object(tools, "write_points", side_effect=fail_names):
            with self.assertRaisesRegex(QgsProcessingException, "Injected"):
                self.run_tool()
        self.assertFalse(Path(self.params["OUTPUT_FOLDER"]).exists())
        self.assertEqual(list(self.root.glob(".reference-packages-*")), [])


if __name__ == "__main__":
    unittest.main()
