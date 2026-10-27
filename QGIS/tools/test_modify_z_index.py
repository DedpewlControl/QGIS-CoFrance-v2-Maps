"""Integration checks for stored Z-index edits, selection, persistence and rollback."""
from contextlib import closing
import gc
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from qgis.core import (
    QgsApplication, QgsDefaultValue, QgsFeature, QgsGeometry, QgsProcessingContext,
    QgsProcessingException, QgsProcessingFeedback, QgsProject, QgsVectorFileWriter,
    QgsVectorLayer,
)

APP = TOOL = None
DIRECTORIES = []


def setUpModule():
    global APP, TOOL
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    APP = QgsApplication.instance() or QgsApplication([], False)
    APP.initQgis()
    sys.path.insert(0, str(Path(QgsApplication.prefixPath()) / "python/plugins"))
    import modify_z_index
    TOOL = modify_z_index


def tearDownModule():
    gc.collect()
    APP.exitQgis()
    for path in DIRECTORIES:
        assert path.resolve().parent == Path(__file__).resolve().parent
        assert path.name.startswith("z_index_test_")
        shutil.rmtree(path)


class ZIndexTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="z_index_test_", dir=Path(__file__).resolve().parent))
        DIRECTORIES.append(self.root)
        self.project = QgsProject()
        self.context = QgsProcessingContext()
        self.context.setProject(self.project)
        self.algorithm = TOOL.ModifyZIndex()
        self.algorithm.initAlgorithm()
        self.feedback = QgsProcessingFeedback()

    def tearDown(self):
        self.context = None
        self.project.clear()
        self.project = None
        gc.collect()

    def layer(self, name, field="z_index", field_type="integer", kind="Point"):
        uri = kind + "?crs=EPSG:4326&field=note:string&field=zoomin:integer&field=" + field + ":" + field_type
        memory = QgsVectorLayer(uri, name, "memory")
        self.assertTrue(memory.isValid())
        for index in range(2):
            feature = QgsFeature(memory.fields())
            feature["note"] = "Keep " + str(index)
            feature["zoomin"] = 8 + index
            feature[field] = ("1" if field_type == "string" else 1) if index else None
            wkt = {"Point": "POINT (1 45)", "LineString": "LINESTRING (1 45, 2 46)",
                   "Polygon": "POLYGON ((1 45, 2 45, 2 46, 1 45))"}[kind]
            feature.setGeometry(QgsGeometry.fromWkt(wkt))
            self.assertTrue(memory.dataProvider().addFeatures([feature])[0])
        path = self.root / (name + ".gpkg")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = name
        saved = QgsVectorFileWriter.writeAsVectorFormatV3(memory, str(path), self.context.transformContext(), options)
        self.assertEqual(saved[0], QgsVectorFileWriter.NoError, saved)
        layer = QgsVectorLayer(str(path) + "|layername=" + name, name, "ogr")
        self.assertTrue(layer.isValid())
        self.project.addMapLayer(layer)
        return layer

    def rows(self, layer):
        # Read saved values independently of the QGIS edit buffer.
        path = layer.source().split("|", 1)[0]
        with closing(sqlite3.connect(path)) as db:
            return db.execute('SELECT * FROM "' + layer.name() + '" ORDER BY fid').fetchall()

    def run_tool(self, layers=None, **overrides):
        values = {"LAYERS": layers or [], "VALUE": 25}
        values.update(overrides)
        valid, message = self.algorithm.checkParameterValues(values, self.context)
        self.assertTrue(valid, message)
        return self.algorithm.processAlgorithm(values, self.context, self.feedback)

    def test_save_multiple_geometry_types_and_field_spellings_preserves_other_data(self):
        layers = [self.layer("Points"), self.layer("Lines", "z-index", "string", "LineString"),
                  self.layer("Polygons", "zIndex", "integer", "Polygon")]
        missing = self.layer("NoZIndex", "other")
        before = {layer.id(): self.rows(layer) for layer in layers + [missing]}
        for layer in layers:
            layer.setDefaultValueDefinition(layer.fields().indexFromName("note"), QgsDefaultValue("'Unwanted default'", True))
        result = self.run_tool(layers + [missing], VALUE=-12)
        self.assertEqual((result["LAYERS_UPDATED"], result["FEATURES_UPDATED"], result["LAYERS_SKIPPED"]), (3, 6, 1))
        for layer in layers:
            field = TOOL.z_index_field(layer, "")
            # SQLite columns are fid, geom, note, zoomin, target field.
            rows = self.rows(layer)
            self.assertEqual([row[:-1] for row in rows], [row[:-1] for row in before[layer.id()]])
            self.assertEqual([row[-1] for row in rows], ["-12" if layer.fields()[field].type() == TOOL.QVariant.String else -12] * 2)
            self.assertFalse(layer.isEditable())
        self.assertEqual(self.rows(missing), before[missing.id()])
        again = self.run_tool(layers + [missing], VALUE=-12)
        self.assertEqual(again["FEATURES_UPDATED"], 0)

    def test_selected_features_and_layer_filter_respected(self):
        selected = self.layer("Selected")
        untouched = self.layer("Unselected")
        selected.setSubsetString('"fid" = 2')
        selected.selectByIds([2])
        before = self.rows(selected)
        result = self.run_tool([selected, untouched], SCOPE=1)
        self.assertEqual((result["FEATURES_UPDATED"], result["LAYERS_SKIPPED"]), (1, 1))
        rows = self.rows(selected)
        self.assertEqual(rows[0], before[0])
        self.assertEqual(rows[1][-1], 25)
        selected.removeSelection()
        # All-feature mode also respects the existing layer filter.
        self.run_tool([selected], VALUE=31)
        self.assertEqual(self.rows(selected)[0], before[0])
        self.assertEqual(self.rows(selected)[1][-1], 31)

    def test_review_mode_preserves_existing_edits_and_undo(self):
        layer = self.layer("Review")
        saved = self.rows(layer)
        self.assertTrue(layer.startEditing())
        note = layer.fields().indexFromName("note")
        self.assertTrue(layer.changeAttributeValue(1, note, "Existing edit", skipDefaultValues=True))
        result = self.run_tool([layer], SAVE_MODE=1)
        self.assertEqual(result["FEATURES_UPDATED"], 2)
        self.assertEqual(self.rows(layer), saved)
        self.assertEqual(next(layer.getFeatures())["z_index"], 25)
        layer.undoStack().undo()
        first = next(layer.getFeatures())
        self.assertEqual(first["note"], "Existing edit")
        self.assertEqual(str(first["z_index"]), "NULL")
        self.assertTrue(layer.isEditable())
        layer.rollBack()

    def test_preflight_rejects_existing_edit_session_or_missing_field_before_writes(self):
        first = self.layer("First")
        second = self.layer("Second")
        before = self.rows(first)
        second.startEditing()
        with self.assertRaisesRegex(QgsProcessingException, "already in edit mode"):
            self.run_tool([first, second])
        self.assertEqual(self.rows(first), before)
        second.rollBack()
        missing = self.layer("Missing", "different")
        with self.assertRaisesRegex(QgsProcessingException, "missing z_index"):
            self.run_tool([first, missing], SKIP_MISSING=False)
        self.assertEqual(self.rows(first), before)
        first.setReadOnly(True)
        with self.assertRaisesRegex(QgsProcessingException, "does not support"):
            self.run_tool([first])
        self.assertEqual(self.rows(first), before)

    def test_failed_attribute_update_rolls_back_current_layer_and_retains_prior_edits(self):
        for review in (False, True):
            layer = self.layer("Failure" + str(review))
            before = self.rows(layer)
            if review:
                layer.startEditing()
                layer.changeAttributeValue(1, layer.fields().indexFromName("note"), "Keep pending")
            actual_change = layer.changeAttributeValue
            calls = []
            def fail_second(*args):
                calls.append(args)
                return actual_change(*args) if len(calls) == 1 else False
            with patch.object(layer, "changeAttributeValue", side_effect=fail_second):
                with self.assertRaisesRegex(QgsProcessingException, "Could not change"):
                    self.run_tool([layer], SAVE_MODE=int(review))
            self.assertEqual(self.rows(layer), before)
            if review:
                self.assertTrue(layer.isEditable())
                self.assertEqual(next(layer.getFeatures())["note"], "Keep pending")
                self.assertEqual(str(next(layer.getFeatures())["z_index"]), "NULL")
                layer.rollBack()
            else:
                self.assertFalse(layer.isEditable())

    def test_cancellation_before_commit_rolls_back_current_layer(self):
        layer = self.layer("Cancel")
        before = self.rows(layer)
        actual_change = layer.changeAttributeValue
        def cancel_after_change(*args):
            result = actual_change(*args)
            self.feedback.cancel()
            return result
        with patch.object(layer, "changeAttributeValue", side_effect=cancel_after_change):
            with self.assertRaisesRegex(QgsProcessingException, "Canceled"):
                self.run_tool([layer])
        self.assertEqual(self.rows(layer), before)
        self.assertFalse(layer.isEditable())

    def gui_selection(self):
        from qgis.PyQt.QtCore import QItemSelectionModel
        from qgis.gui import QgsLayerTreeView
        from qgis.core import QgsLayerTreeModel
        self.project.clear()
        self.project = QgsProject.instance()
        self.project.clear()
        self.context.setProject(self.project)
        view = QgsLayerTreeView()
        model = QgsLayerTreeModel(self.project.layerTreeRoot())
        view.setModel(model)
        def select(*nodes):
            view.selectionModel().clearSelection()
            for node in nodes:
                view.selectionModel().select(view.node2index(node),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows)
            APP.processEvents()
        return view, model, select

    def test_native_tree_highlights_captured_before_selection_clears_and_algorithm_clones(self):
        from types import SimpleNamespace
        from qgis.PyQt.QtWidgets import QDialog
        from processing.gui.wrappers import WidgetWrapperFactory
        view, model, select = self.gui_selection()
        first, second, untouched = (self.layer(name) for name in ("FirstUI", "SecondUI", "UntouchedUI"))
        group = self.project.layerTreeRoot().addGroup("Selected group")
        nested = group.addGroup("Nested")
        # Reparent cloned tree nodes without changing layer ownership.
        for layer, parent in ((first, group), (second, nested)):
            old = self.project.layerTreeRoot().findLayer(layer.id())
            parent.addChildNode(old.clone())
            old.parent().removeChildNode(old)
        select(group, self.project.layerTreeRoot().findLayer(second.id()))
        dialog = type("AlgorithmDialog", (QDialog,), {})()
        with patch("qgis.utils.iface", SimpleNamespace(layerTreeView=lambda: view)):
            # Only the interface boundary is adapted; selection expansion uses
            # a real QGIS tree, model, nested groups and native selection model.
            self.assertEqual({layer.id() for layer in TOOL.selected_project_layers()}, {first.id(), second.id()})
            wrapper = WidgetWrapperFactory.create_wrapper(self.algorithm.parameterDefinition("LAYERS"), dialog)
            self.assertEqual(set(wrapper.value()), {first.id(), second.id()})
            self.assertIn("2", wrapper.widget.leText.text())
            self.assertIn("FirstUI", wrapper.widget.leText.toolTip())
            select()  # The tree is no longer highlighted when Processing runs.
            self.assertEqual(TOOL.selected_project_layers(), [])
            clone = self.algorithm.create()
            result, ok = clone.run({"LAYERS": wrapper.value(), "VALUE": 62}, self.context, self.feedback)
            self.assertTrue(ok)
            self.assertEqual(result["FEATURES_UPDATED"], 4)
            self.assertEqual([row[-1] for row in self.rows(first)], [62, 62])
            self.assertEqual([row[-1] for row in self.rows(second)], [62, 62])
            self.assertEqual([row[-1] for row in self.rows(untouched)], [None, 1])
        dialog.close()
        wrapper.widget.close()
        view.setModel(None)

    def test_highlight_button_refreshes_targets_and_manual_picker_remains_stable(self):
        from types import SimpleNamespace
        from qgis.PyQt.QtWidgets import QDialog
        from processing.gui.wrappers import WidgetWrapperFactory
        view, model, select = self.gui_selection()
        first = self.layer("ZuluUI")
        select(self.project.layerTreeRoot().findLayer(first.id()))
        dialog = type("AlgorithmDialog", (QDialog,), {})()
        with patch("qgis.utils.iface", SimpleNamespace(layerTreeView=lambda: view)):
            wrapper = WidgetWrapperFactory.create_wrapper(self.algorithm.parameterDefinition("LAYERS"), dialog)
            self.assertEqual(wrapper.value(), [first.id()])
            second = self.layer("AlphaUI")  # Changes the picker sort order.
            select(self.project.layerTreeRoot().findLayer(second.id()))
            wrapper.highlight_button.click()
            self.assertEqual(wrapper.value(), [second.id()])
            wrapper.setValue([first.id()])  # Programmatic/manual picker override.
            wrapper.refresh()
            self.assertEqual(wrapper.value(), [first.id()])
            wrapper.widget.selectedoptions = [wrapper._option_ids.index(second.id())]
            wrapper.widget.selectionChanged.emit()
            self.assertEqual(wrapper.value(), [second.id()])
            select()
            wrapper.highlight_button.click()
            self.assertEqual(wrapper.value(), [])
        dialog.close()
        wrapper.widget.close()
        view.setModel(None)

    def test_panel_selection_deduplicated_and_explicit_field_override(self):
        layer = self.layer("Custom", "drawing_order")
        with patch.object(TOOL, "selected_project_layers", return_value=[layer, layer]) as picker:
            result = self.run_tool(FIELD="drawing_order", VALUE=0)
        picker.assert_called_once()
        self.assertEqual((result["LAYERS_UPDATED"], result["FEATURES_UPDATED"]), (1, 2))
        self.assertEqual([row[-1] for row in self.rows(layer)], [0, 0])
        report = json.loads(result["REPORT"])
        self.assertEqual(report[0]["field"], "drawing_order")
        self.assertEqual(report[0]["status"], "saved")


if __name__ == "__main__":
    unittest.main()
