"""Integration checks for the folder/group tool, run with QGIS Python."""

import gc
import os
from pathlib import Path
import tempfile
import unittest

from qgis.core import (
    QgsApplication, QgsProcessingAlgorithm, QgsProcessingContext,
    QgsProcessingException, QgsProcessingFeedback, QgsProject, QgsVectorLayer,
)

APP = None
TOOL = None
RUNTIME_TEMP = None


def setUpModule():
    global APP, TOOL, RUNTIME_TEMP
    RUNTIME_TEMP = tempfile.TemporaryDirectory(prefix="map_structure_runtime_", dir=Path(__file__).resolve().parent)
    os.environ["TEMP"] = RUNTIME_TEMP.name
    os.environ["TMP"] = RUNTIME_TEMP.name
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    APP = QgsApplication.instance() or QgsApplication([], False)
    APP.initQgis()
    import create_map_structure
    TOOL = create_map_structure


def tearDownModule():
    APP.exitQgis()
    assert Path(RUNTIME_TEMP.name).resolve().parent == Path(__file__).resolve().parent
    RUNTIME_TEMP.cleanup()


class MapStructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="map_structure_", dir=Path(__file__).resolve().parent)
        self.root = Path(self.temp.name)
        self.project = QgsProject()
        self.context = QgsProcessingContext()
        self.context.setProject(self.project)
        self.algorithm = TOOL.CreateMapStructure()
        self.algorithm.initAlgorithm()
        self.params = {"MAP_NAME": "La Rochelle", "FIR": 0, "SOURCE_ROOT": str(self.root),
                       "ALTITUDE": 1, "PROCEDURES": True}

    def tearDown(self):
        self.context = None
        self.project.clear()
        self.project = None
        gc.collect()
        self.assertEqual(self.root.resolve().parent, Path(__file__).resolve().parent)
        self.assertTrue(self.root.name.startswith("map_structure_"))
        self.temp.cleanup()

    def run_tool(self, params=None):
        params = params or self.params
        valid, message = self.algorithm.checkParameterValues(params, self.context)
        self.assertTrue(valid, message)
        result, ok = self.algorithm.run(params, self.context, QgsProcessingFeedback())
        self.assertTrue(ok)
        return result

    def test_disk_tree_matches_project_and_repeat_preserves_layers(self):
        self.assertTrue(self.algorithm.flags() & QgsProcessingAlgorithm.FlagNoThreading)
        result = self.run_tool()
        self.assertEqual((result["FOLDERS_CREATED"], result["GROUPS_CREATED"]), (8, 9))
        map_folder = self.root / "LFBB" / "La Rochelle"
        expected = ["0_CCA_La_Rochelle", "1_CCA_La_Rochelle_AMSR",
                    "2_CCA_La_Rochelle_Procedures", "3_CCA_La_Rochelle_Navaids_Names",
                    "3_CCA_La_Rochelle_FIX_Names", "4_CCA_La_Rochelle_VFR"]
        self.assertEqual({p.name for p in map_folder.iterdir()}, set(expected))
        cca = self.project.layerTreeRoot().findGroup("CCA")
        fir = cca.findGroup("LFBB - CCA Maps")
        group = fir.findGroup("La Rochelle")
        self.assertEqual([c.name() for c in group.children()], list(reversed(expected)))
        self.assertTrue(self.project.isDirty())
        layer = QgsVectorLayer("Point?crs=EPSG:4326", "User layer", "memory")
        self.project.addMapLayer(layer, False)
        group.findGroup(expected[0]).addLayer(layer)
        marker = map_folder / expected[0] / "existing.txt"
        marker.write_text("preserve", encoding="utf-8")
        self.project.setDirty(False)
        again = self.run_tool()
        self.assertEqual((again["FOLDERS_CREATED"], again["GROUPS_CREATED"]), (0, 0))
        self.assertFalse(self.project.isDirty())
        self.assertIsNotNone(group.findLayer(layer.id()))
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
        self.assertEqual([c.name() for c in group.children()], list(reversed(expected)))

    def test_save_and_reopen_retains_groups(self):
        self.run_tool()
        path = self.root / "test.qgz"
        self.assertTrue(self.project.write(str(path)))
        reopened = QgsProject()
        self.assertTrue(reopened.read(str(path)))
        group = reopened.layerTreeRoot().findGroup("La Rochelle")
        self.assertEqual(len(group.children()), 6)
        reopened.clear()

    def test_amg_altitude_folder_and_group(self):
        result=self.run_tool(dict(self.params,ALTITUDE=TOOL.ALTITUDE_TYPES.index("AMG")))
        expected="1_CCA_La_Rochelle_AMG"
        self.assertTrue((Path(result["MAP_FOLDER"])/expected).is_dir())
        group=self.project.layerTreeRoot().findGroup("La Rochelle")
        self.assertIsNotNone(group.findGroup(expected))
        self.assertIsNone(group.findGroup("1_CCA_La_Rochelle_MVA"))
        self.assertIsNone(group.findGroup("1_CCA_La_Rochelle_AMSR"))

    def test_optional_sections(self):
        result = self.run_tool(dict(self.params, ALTITUDE=TOOL.ALTITUDE_TYPES.index("None"), PROCEDURES=False))
        path = Path(result["MAP_FOLDER"])
        self.assertEqual(len(list(path.iterdir())), 4)
        self.assertFalse(any(p.name.startswith(("1_", "2_")) for p in path.iterdir()))

    def test_conflicting_file_rejected_before_group_creation(self):
        folder = self.root / "LFBB" / "La Rochelle"
        folder.mkdir(parents=True)
        blocker = folder / "4_CCA_La_Rochelle_VFR"
        blocker.write_text("keep", encoding="utf-8")
        with self.assertRaises(QgsProcessingException):
            self.algorithm.processAlgorithm(self.params, self.context, QgsProcessingFeedback())
        self.assertEqual(self.project.layerTreeRoot().children(), [])
        self.assertEqual(blocker.read_text(), "keep")
        self.assertEqual(list(folder.iterdir()), [blocker])

    def test_map_types_create_separate_roots_without_approach_subfolders(self):
        for map_type in ("Aerodrome", "Enroute"):
            params = dict(self.params, MAP_TYPE=TOOL.MAP_TYPES.index(map_type))
            result = self.run_tool(params)
            folder = self.root / "LFBB" / map_type / "La Rochelle"
            self.assertEqual(Path(result["MAP_FOLDER"]), folder)
            self.assertEqual(list(folder.iterdir()), [])
            self.assertEqual(result["LAYOUT_STATUS"], "root_only_pending_layout")
            expected = map_type + "/LFBB - " + map_type + " Maps/La Rochelle"
            self.assertEqual(result["GROUP_PATH"], expected)
            group = self.project.layerTreeRoot().findGroup(map_type).findGroup("La Rochelle")
            self.assertEqual(group.children(), [])
            layer = QgsVectorLayer("Point?crs=EPSG:4326", "Existing", "memory")
            self.project.addMapLayer(layer, False)
            group.addLayer(layer)
            (folder / "existing.txt").write_text("keep", encoding="utf-8")
            again = self.run_tool(params)
            self.assertEqual((again["FOLDERS_CREATED"], again["GROUPS_CREATED"]), (0, 0))
            self.assertIsNotNone(group.findLayer(layer.id()))
            self.assertEqual((folder / "existing.txt").read_text(), "keep")
        self.assertIsNone(self.project.layerTreeRoot().findGroup("CCA"))
        approach = self.run_tool()
        self.assertEqual(Path(approach["MAP_FOLDER"]), self.root / "LFBB" / "La Rochelle")
        self.assertEqual(approach["LAYOUT_STATUS"], "complete")

    def test_invalid_map_name_is_rejected(self):
        for name in ("../Other", r"..\Other", "CON", "bad:name", ".", ".."):
            with self.subTest(name=name), self.assertRaises(QgsProcessingException):
                self.algorithm.processAlgorithm(dict(self.params, MAP_NAME=name),
                                                self.context, QgsProcessingFeedback())
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.project.layerTreeRoot().children(), [])


if __name__ == "__main__":
    unittest.main()
