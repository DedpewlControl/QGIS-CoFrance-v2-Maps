"""Batch planning tests and optional integration tests with the QGIS runtime."""

import json
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .export_plan import plan_group_exports


class Layer:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class ExportPlanTests(unittest.TestCase):
    def test_parent_selection_exports_each_numbered_subgroup(self):
        names = ["0_CCA_Limoges", "1_CCA_Limoges_MVA",
                 "3_CCA_Limoges_Navaids_Names", "3_CCA_Limoges_FIX_Names"]
        entries = [(Layer("layer"), ("LFBB", "Limoges", name)) for name in names]
        self.assertEqual([name for name, _ in plan_group_exports(entries)],
                         [name + ".geojson" for name in names])

    def test_vfr_splits_names_and_combines_arp_variants(self):
        names = ["Limoges VFR Point", "CCA_Limoges_ARPs", "CCA_Limoges_ARP_Names",
                 "CCA_Limoges_ARPs_Names", "CCA_Limoges ARPs", "CCA_Limoges_VFR_ARPs"]
        layers = [Layer(name) for name in names]
        batches = dict(plan_group_exports(
            (layer, ("Limoges", "4_CCA_Limoges_VFR")) for layer in layers
        ))
        self.assertEqual(batches["4_CCA_Limoges_VFR_Point_Names.geojson"], layers[:1])
        self.assertEqual(batches["4_CCA_Limoges_ARPs.geojson"], layers[1:])

    def test_nested_vfr_groups_belong_to_outer_vfr_group(self):
        layer = Layer("EGA_Paris_VFR_Point_Names")
        batches = plan_group_exports([(layer, ("Paris", "4_EGA_Paris_VFR",
                                              "5_EGA_Paris_VFR_Point_Names"))])
        self.assertEqual(batches, [("4_EGA_Paris_VFR_Point_Names.geojson", [layer])])

    def test_legacy_arp_group_names_export_as_arps(self):
        layer = Layer("ARP symbols")
        for suffix in ["VFR_ARPs", "VFR_ARP", "ARPs_Names", "VFR_ARP_Names", "ARP"]:
            with self.subTest(suffix=suffix):
                batches = plan_group_exports([(layer, ("Lille", "4_CCA_Lille_" + suffix))])
                self.assertEqual(batches, [("4_CCA_Lille_ARPs.geojson", [layer])])

    def test_nested_layers_keep_supplied_draw_order(self):
        layers = [Layer("bottom"), Layer("top")]
        entries = [(layers[0], ("Limoges", "0_CCA_Limoges", "Symbols")),
                   (layers[1], ("Limoges", "0_CCA_Limoges"))]
        self.assertEqual(plan_group_exports(entries), [("0_CCA_Limoges.geojson", layers)])

    def test_decimal_numbered_groups_own_their_nested_layers(self):
        layer = Layer("procedure")
        self.assertEqual(plan_group_exports([(layer, ("Paris", "2.1_EGA_Paris_Procedures", "Lines"))]),
                         [("2.1_EGA_Paris_Procedures.geojson", [layer])])

    def test_duplicate_filenames_across_parent_groups_are_rejected(self):
        entries = [(Layer("one"), ("A", "0_CCA_Test")),
                   (Layer("two"), ("B", "0_cca_test"))]
        with self.assertRaisesRegex(ValueError, "Different groups"):
            plan_group_exports(entries)

    def test_no_numbered_group_uses_nearest_group_or_layer(self):
        first, second = Layer("one"), Layer("standalone")
        self.assertEqual(plan_group_exports([(first, ("Region", "Town")), (second, ())]),
                         [("Town.geojson", [first]), ("standalone.geojson", [second])])

    def test_unsafe_names_are_sanitized_and_collisions_rejected(self):
        self.assertEqual(plan_group_exports([(Layer("x"), ("CON",))])[0][0], "_CON.geojson")
        with self.assertRaisesRegex(ValueError, "Different groups"):
            plan_group_exports([(Layer("x"), ("0_A/B",)), (Layer("y"), ("0_A:B",))])

    def test_empty_selection_has_no_batches(self):
        self.assertEqual(plan_group_exports([]), [])


try:
    from qgis.core import QgsApplication, QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import QMessageBox
    from .geojson_styler import GeoJSONStyler
    from .geojson_styler_dialog import GeoJSONStylerDialog
    from .cofrance_schema import CoFranceSchemaError
    HAS_QGIS = True
except ImportError:
    HAS_QGIS = False


@unittest.skipUnless(HAS_QGIS, "Requires the QGIS Python runtime")
class QgisBatchExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QgsApplication.instance()
        if cls.app is None:
            cls.app = QgsApplication([], False)
            cls.app.initQgis()

    def setUp(self):
        QgsProject.instance().clear()
        self.parent = QgsProject.instance().layerTreeRoot().addGroup("Limoges")
        self.base = self.parent.addGroup("0_CCA_Limoges")
        self.vfr = self.parent.addGroup("4_CCA_Limoges_VFR")
        self.add_layer(self.base, "Base labels")
        self.add_layer(self.vfr, "CCA_Limoges_ARP_Names")
        self.add_layer(self.vfr, "CCA_Limoges_ARPs", symbol=True)
        self.add_layer(self.vfr, "Limoges VFR Point")
        self.dialog = GeoJSONStylerDialog()
        self.dialog.populate_layers()
        self.dialog.layer_tree.topLevelItem(0).setCheckState(0, Qt.Checked)
        self.exporter = GeoJSONStyler.__new__(GeoJSONStyler)
        self.exporter.iface = None
        self.exporter.dlg = self.dialog

    def tearDown(self):
        self.dialog.close()
        if hasattr(self, "view"):
            self.view.setModel(None)
            self.view.close()
        QgsProject.instance().clear()

    def add_layer(self, group, name, symbol=False):
        fields = ["name", "symbol_type"] if symbol else ["uuid", "text"]
        fields += ["activation_icao", "activation_arr", "activation_dep"]
        uri = "Point?crs=EPSG:4326" + "".join("&field=" + field + ":string" for field in fields)
        layer = QgsVectorLayer(uri, name, "memory")
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt("POINT (1.23456789 45.67891234)"))
        if symbol:
            feature["name"], feature["symbol_type"] = name, "aerodrome"
        else:
            feature["uuid"], feature["text"] = name, name
        layer.dataProvider().addFeatures([feature])
        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        return layer

    def native_selection(self):
        from types import SimpleNamespace
        from qgis.PyQt.QtCore import QItemSelectionModel
        from qgis.gui import QgsLayerTreeView
        from qgis.core import QgsLayerTreeModel
        self.view = QgsLayerTreeView()
        self.model = QgsLayerTreeModel(QgsProject.instance().layerTreeRoot())
        self.view.setModel(self.model)
        self.interface = SimpleNamespace(layerTreeView=lambda: self.view, mainWindow=lambda: None)
        def select(*nodes):
            self.view.selectionModel().clearSelection()
            for node in nodes:
                self.view.selectionModel().select(self.view.node2index(node),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows)
            self.app.processEvents()
        return select

    def reopen_with_highlights(self):
        self.dialog.close()
        self.dialog = GeoJSONStylerDialog(iface=self.interface)
        self.dialog.populate_layers()
        self.exporter.dlg = self.dialog

    def test_plugin_open_preselects_highlighted_parent_and_retains_batches(self):
        select = self.native_selection()
        select(self.parent, self.vfr.children()[0])  # Group + child must not duplicate targets.
        module = importlib.import_module(GeoJSONStyler.__module__)
        self.dialog.close()
        self.exporter.iface = self.interface
        with patch.object(module.GeoJSONStylerDialog, "exec_", return_value=0):
            self.exporter.run()
        self.dialog = self.exporter.dlg
        self.assertEqual(len(self.dialog.get_selected_layers()), 4)
        self.assertEqual(self.dialog.selection_summary.text(), "4 layers selected")
        self.assertEqual(self.dialog.layer_tree.topLevelItem(0).checkState(0), Qt.Checked)
        select()  # Checked export targets persist after the highlight clears.
        self.assertEqual(len(self.dialog.get_selected_layers()), 4)
        self.assertCountEqual([name for name, _ in self.dialog.get_export_batches()],
                              ["0_CCA_Limoges.geojson", "4_CCA_Limoges_VFR_Point_Names.geojson",
                               "4_CCA_Limoges_ARPs.geojson"])
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            counts = dict(self.exporter.export_batches(self.dialog.get_export_batches(), directory))
            self.assertEqual(counts["4_CCA_Limoges_ARPs.geojson"], 2)
            self.assertEqual(sum(counts.values()), 4)

    def test_highlighted_single_layers_only_check_their_own_rows(self):
        select = self.native_selection()
        select(self.base.children()[0], self.vfr.children()[0])
        self.reopen_with_highlights()
        self.assertEqual([layer.name() for layer in self.dialog.get_selected_layers()],
                         ["CCA_Limoges_ARP_Names", "Base labels"])
        self.assertEqual(self.dialog.layer_tree.topLevelItem(0).checkState(0), Qt.PartiallyChecked)
        self.assertEqual(self.dialog.selection_summary.text(), "2 layers selected")
        self.assertEqual({name: len(layers) for name, layers in self.dialog.get_export_batches()},
                         {"0_CCA_Limoges.geojson": 1, "4_CCA_Limoges_ARPs.geojson": 1})
        # Users can still adjust export checkboxes after loading the highlights.
        self.dialog.layer_tree.topLevelItem(0).child(1).child(2).setCheckState(0, Qt.Checked)
        self.assertEqual(len(self.dialog.get_selected_layers()), 3)
        self.assertEqual(self.dialog.selection_summary.text(), "3 layers selected")

    def test_highlight_refresh_rebuilds_tree_and_replaces_previous_checks(self):
        select = self.native_selection()
        select(self.parent)
        self.reopen_with_highlights()
        added = self.add_layer(self.base, "Added after opening")
        select(self.base.children()[-1])
        self.dialog.use_highlighted_button.click()
        self.assertEqual(self.dialog.get_selected_layers(), [added])
        self.assertEqual(self.dialog.selection_summary.text(), "1 layer selected")
        select()
        self.dialog.use_highlighted_button.click()
        self.assertEqual(self.dialog.get_selected_layers(), [])
        self.assertEqual(self.dialog.selection_summary.text(), "0 layers selected")
        self.dialog.select_all_button.click()
        self.assertEqual(len(self.dialog.get_selected_layers()), 5)
        self.dialog.select_none_button.click()
        self.assertEqual(self.dialog.get_selected_layers(), [])
        self.assertEqual(self.dialog.selection_summary.text(), "0 layers selected")

    def test_highlight_matches_exact_tree_occurrence_for_repeated_layer(self):
        select = self.native_selection()
        # A duplicate group name and repeated layer ID must still match only
        # the particular tree row highlighted by the user.
        duplicate_group = self.parent.addGroup(self.base.name())
        duplicate_node = self.base.children()[0].clone()
        duplicate_group.addChildNode(duplicate_node)
        select(duplicate_node)
        self.reopen_with_highlights()
        self.assertEqual(len(self.dialog.get_selected_layers()), 1)
        parent_item = self.dialog.layer_tree.topLevelItem(0)
        self.assertEqual(parent_item.child(0).child(0).checkState(0), Qt.Unchecked)
        self.assertEqual(parent_item.child(2).child(0).checkState(0), Qt.Checked)

    def test_parent_checkbox_batch_files_and_contents(self):
        self.assertTrue(self.dialog.is_batch_export())
        self.assertEqual([layer.name() for layer in self.dialog.get_selected_layers()],
                         ["Limoges VFR Point", "CCA_Limoges_ARPs", "CCA_Limoges_ARP_Names", "Base labels"])
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            counts = dict(self.exporter.export_batches(self.dialog.get_export_batches(), directory))
            self.assertEqual(counts, {"0_CCA_Limoges.geojson": 1,
                                      "4_CCA_Limoges_VFR_Point_Names.geojson": 1,
                                      "4_CCA_Limoges_ARPs.geojson": 2})
            data = json.loads((Path(directory) / "4_CCA_Limoges_ARPs.geojson").read_text())
            self.assertEqual(["pointStyle" in f["properties"] for f in data["features"]], [True, False])
            self.assertEqual(data["features"][0]["geometry"]["coordinates"], [[1.234568, 45.678912]])

    def test_partial_selection_only_exports_checked_layers(self):
        self.dialog._set_all_checked(Qt.Unchecked)
        vfr_item = self.dialog.layer_tree.topLevelItem(0).child(1)
        vfr_item.child(0).setCheckState(0, Qt.Checked)
        batches = self.dialog.get_export_batches()
        self.assertEqual([(name, len(layers)) for name, layers in batches],
                         [("4_CCA_Limoges_ARPs.geojson", 1)])

    def test_validation_failure_does_not_replace_existing_or_create_partial_files(self):
        invalid = QgsVectorLayer("Point?crs=EPSG:4326", "Invalid", "memory")
        valid = self.dialog.get_selected_layers()[-1]
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            existing = Path(directory) / "existing.geojson"
            existing.write_text("original")
            with self.assertRaises(CoFranceSchemaError):
                self.exporter.export_batches([("existing.geojson", [valid]),
                                               ("invalid.geojson", [invalid])], directory)
            self.assertEqual(existing.read_text(), "original")
            self.assertEqual(list(Path(directory).iterdir()), [existing])

    def test_overwrite_decline_keeps_existing_files(self):
        module = importlib.import_module(GeoJSONStyler.__module__)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            existing = Path(directory) / "0_CCA_Limoges.geojson"
            existing.write_text("original")
            with patch.object(module.QFileDialog, "getExistingDirectory", return_value=directory), \
                 patch.object(module.QMessageBox, "question", return_value=QMessageBox.No), \
                 patch.object(self.exporter, "export_batches") as export:
                self.exporter.process_layers()
                export.assert_not_called()
            self.assertEqual(existing.read_text(), "original")

    def test_single_file_mode_still_combines_selection(self):
        self.dialog.batch_export_checkbox.setChecked(False)
        module = importlib.import_module(GeoJSONStyler.__module__)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            output = Path(directory) / "combined.geojson"
            with patch.object(module.QFileDialog, "getSaveFileName", return_value=(str(output), "")), \
                 patch.object(module.QMessageBox, "information"):
                self.exporter.process_layers()
            self.assertEqual(len(json.loads(output.read_text())["features"]), 4)

    def test_batch_export_updates_parent_manifest(self):
        module = importlib.import_module(GeoJSONStyler.__module__)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"$schema": "unchanged", "LFBB": {
                "LFBL": ["0_CCA_Limoges", "unselected_map"], "LFBD": ["0_CCA_Aquitaine"]}}))
            output = root / "LFBB" / "LFBL"
            output.mkdir(parents=True)
            with patch.object(module.QFileDialog, "getExistingDirectory", return_value=str(output)), \
                 patch.object(module.QMessageBox, "question", return_value=QMessageBox.Yes), \
                 patch.object(module.QMessageBox, "information") as success, \
                 patch.object(module.QMessageBox, "critical") as failure:
                self.exporter.process_layers()
                failure.assert_not_called()
                success.assert_called_once()
            data = json.loads(manifest.read_text())
            self.assertEqual(data["LFBB"]["LFBL"], ["0_CCA_Limoges", "unselected_map",
                             "4_CCA_Limoges_VFR_Point_Names", "4_CCA_Limoges_ARPs"])
            self.assertEqual(data["LFBB"]["LFBD"], ["0_CCA_Aquitaine"])
            self.assertEqual(data["$schema"], "unchanged")
            self.assertEqual(len(list(output.glob("*.geojson"))), 3)
            self.assertFalse((output / "manifest.json").exists())

    def test_manifest_changes_are_not_committed_if_a_layer_is_invalid(self):
        from .manifest_export import prepare_manifest_update
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps({"LFBB": {"LFBL": ["0_CCA_Limoges"]}}))
            original = manifest.read_bytes()
            update = prepare_manifest_update(directory, ["4_CCA_Limoges_ARPs.geojson"])
            invalid = QgsVectorLayer("Point?crs=EPSG:4326", "Invalid", "memory")
            with self.assertRaises(CoFranceSchemaError):
                self.exporter.export_batches([("4_CCA_Limoges_ARPs.geojson", [invalid])],
                                             directory, manifest_update=update)
            self.assertEqual(manifest.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [manifest])

    def test_manifest_legacy_cleanup_is_written_even_without_added_entries(self):
        module = importlib.import_module(GeoJSONStyler.__module__)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps({"LFBB": {"LFBL": ["0_CCA_Limoges",
                "4_CCA_Limoges_VFR_ARPs", "4_CCA_Limoges_ARPs",
                "4_CCA_Limoges_VFR_Point_Names"]}}))
            with patch.object(module.QFileDialog, "getExistingDirectory", return_value=directory), \
                 patch.object(module.QMessageBox, "question", return_value=QMessageBox.Yes), \
                 patch.object(module.QMessageBox, "information"), \
                 patch.object(module.QMessageBox, "critical") as failure:
                self.exporter.process_layers()
                failure.assert_not_called()
            self.assertEqual(json.loads(manifest.read_text())["LFBB"]["LFBL"],
                             ["0_CCA_Limoges", "4_CCA_Limoges_ARPs", "4_CCA_Limoges_VFR_Point_Names"])
            self.assertEqual(len(list(Path(directory).glob("*.geojson"))), 3)

    def test_changed_manifest_stops_before_output_replacement(self):
        from .manifest_export import prepare_manifest_update
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps({"LFBB": {"LFBL": ["0_CCA_Limoges"]}}))
            update = prepare_manifest_update(directory, ["4_CCA_Limoges_ARPs.geojson"])
            manifest.write_text('{"another_writer": true}')
            with self.assertRaisesRegex(ValueError, "changed during export"):
                self.exporter.export_batches(self.dialog.get_export_batches(), directory,
                                             manifest_update=update)
            self.assertEqual(list(Path(directory).iterdir()), [manifest])

    def test_manifest_update_can_be_disabled(self):
        module = importlib.import_module(GeoJSONStyler.__module__)
        self.dialog.update_manifest_checkbox.setChecked(False)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text("invalid JSON, ignored when disabled")
            with patch.object(module.QFileDialog, "getExistingDirectory", return_value=directory), \
                 patch.object(module.QMessageBox, "information"), \
                 patch.object(module.QMessageBox, "critical") as failure:
                self.exporter.process_layers()
                failure.assert_not_called()
            self.assertEqual(manifest.read_text(), "invalid JSON, ignored when disabled")
            self.assertEqual(len(list(Path(directory).glob("*.geojson"))), 3)


if __name__ == "__main__":
    unittest.main()
