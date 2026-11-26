"""Integration checks for focused dialogs, routing and profile migration."""
import json
from pathlib import Path
import unittest

from qgis.core import QgsProcessingFeedback
import test_generate_navigation_packages as nav


def setUpModule():
    nav.setUpModule()


def tearDownModule():
    nav.tearDownModule()


class FocusedToolTests(unittest.TestCase):
    setUp = nav.NavigationTests.setUp
    tearDown = nav.NavigationTests.tearDown
    p = nav.NavigationTests.p
    line = nav.NavigationTests.line
    layer = nav.NavigationTests.layer
    points = nav.NavigationTests.points
    load = nav.NavigationTests.load

    def run_entry(self, filename, values):
        from processing.script.ScriptUtils import loadAlgorithm
        algorithm = loadAlgorithm("focused_" + Path(filename).stem,
                                  str(Path(__file__).parent / filename))
        self.assertIsNotNone(algorithm, filename)
        algorithm.initAlgorithm()
        valid, message = algorithm.checkParameterValues(values, self.context)
        self.assertTrue(valid, message)
        result, ok = algorithm.run(values, self.context, QgsProcessingFeedback())
        self.assertTrue(ok, filename)
        return algorithm, result

    def test_airway_entry_routes_high_and_low_and_ignores_stale_procedure_mode(self):
        for level, name in ((0, "A2"), (1, "L1")):
            params = dict(self.params, LEVEL=level, AIRWAY_SELECTION=1, AIRWAYS=name,
                          MODE=5, PERIMETER=None, OUTPUT_FOLDER=str(self.root / str(level)))
            if level == 0:
                for key in ("BASE_NAME", "WORK_CRS", "TOLERANCE", "DX", "DY"):
                    params.pop(key, None)
            algorithm, result = self.run_entry("generate_airways.py", params)
            if level == 0:
                self.assertTrue(Path(result["LINES"]).name.startswith("Airways_HIGH_AIRWAYS_"))
            report = json.loads(Path(result["REPORT"]).read_text())
            self.assertEqual(report["mode"], ("HIGH_AIRWAYS", "LOW_AIRWAYS")[level])
            self.assertEqual(report["selection_method"], "airway_list")
            self.assertEqual(result["LINE_COUNT"], 1)
            self.assertGreater(result["POINT_COUNT"], 0)
            self.assertIsNone(algorithm.parameterDefinition("ICAO"))

    def test_sid_and_star_entries_route_reference_and_manual_sources(self):
        for kind in ("sid", "star"):
            for source in (0, 1):
                params = dict(self.params, SOURCE=source, MODE=0, AIRWAY_SELECTION=0,
                              PERIMETER=None, MANUAL_POINTS="VOR:CNA > NDB:NDB1 > FIXB",
                              OUTPUT_FOLDER=str(self.root / (kind + str(source))))
                algorithm, result = self.run_entry("generate_" + kind + ".py", params)
                report = json.loads(Path(result["REPORT"]).read_text())
                self.assertEqual(report["mode"], kind.upper())
                self.assertEqual(report["manual"], bool(source))
                self.assertEqual(report["selection_method"], "airport_runway")
                self.assertIsNone(report["perimeter_wgs84"])
                self.assertEqual(result["LINE_COUNT"], 1)
                self.assertIsNone(algorithm.parameterDefinition("PERIMETER"))
                self.assertIsNone(algorithm.parameterDefinition("AIRWAYS"))
                if source:
                    self.assertEqual({row["ident"] for row in self.load(result["SYMBOLS"]).getFeatures()},
                                     {"CNA", "NDB1", "FIXB"})

    def test_installer_loads_current_tools_and_archives_superseded_entries(self):
        import install_cofrance_tools as installer
        from processing.script.ScriptUtils import loadAlgorithm
        profile = self.root / "profile"
        scripts = profile / "processing" / "scripts"
        scripts.mkdir(parents=True)
        previous = "# user copy\nclass GenerateNavigationPackages: pass\n"
        (scripts / "generate_navigation_packages.py").write_text(previous)
        (scripts / "generate_fix_names.py").write_text("# previous symbol name tool\n")
        folder_previous = (Path(__file__).parent / "generate_point_names_folder.py").read_text()
        (scripts / "generate_point_names_folder.py").write_text(folder_previous)
        unrelated = scripts / "unrelated.py"
        unrelated.write_text("# preserve")
        preview = installer.install_cofrance_tools(dry_run=True, profile_directory=str(profile))
        self.assertFalse(Path(preview["backup_directory"]).exists())
        self.assertEqual((scripts / "generate_navigation_packages.py").read_text(), previous)
        result = installer.install_cofrance_tools(profile_directory=str(profile))
        self.assertEqual(len(result["installed"]), 13)
        self.assertEqual(result["archived_legacy"], ["generate_navigation_packages.py", "generate_point_names_folder.py"])
        self.assertFalse((scripts / "generate_point_names_folder.py").exists())
        self.assertEqual((Path(result["backup_directory"]) / "generate_point_names_folder.py").read_text(), folder_previous)
        self.assertFalse((scripts / "generate_navigation_packages.py").exists())
        self.assertEqual((Path(result["backup_directory"]) / "generate_navigation_packages.py").read_text(), previous)
        self.assertEqual(unrelated.read_text(), "# preserve")
        identifiers = set()
        for filename in result["installed"]:
            algorithm = loadAlgorithm("installed_" + Path(filename).stem, str(scripts / filename))
            self.assertIsNotNone(algorithm, filename)
            algorithm.initAlgorithm()
            identifiers.add(algorithm.name())
        self.assertEqual(len(identifiers), 13)
        self.assertIn("modify_cofrance_z_index", identifiers)
        self.assertTrue({"generate_cofrance_navaid_packages", "generate_cofrance_airport_packages", "generate_cofrance_runway_packages"}.issubset(identifiers))
        self.assertIn("generate_fix_names", identifiers)
        self.assertNotIn("generate_cofrance_point_names_folder", identifiers)
        self.assertNotIn("generate_cofrance_navigation_packages", identifiers)


if __name__ == "__main__":
    unittest.main()
