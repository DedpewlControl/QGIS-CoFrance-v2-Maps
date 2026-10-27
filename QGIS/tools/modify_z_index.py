"""Set the stored z-index attribute on chosen CoFrance vector layers."""

import json
import math
import re
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QPushButton
from processing.gui.wrappers import DIALOG_STANDARD, MultipleLayerWidgetWrapper
from qgis.core import (
    QgsFeatureRequest, QgsFields, QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingException, QgsProcessingOutputNumber, QgsProcessingOutputString,
    QgsProcessingParameterBoolean, QgsProcessingParameterEnum,
    QgsProcessingParameterMultipleLayers, QgsProcessingParameterNumber,
    QgsProcessingParameterString, QgsProcessingUtils, QgsProject, QgsVectorDataProvider, QgsVectorLayer,
)


INTEGER_TYPES = (QVariant.Int, QVariant.UInt, QVariant.LongLong, QVariant.ULongLong)
SUPPORTED_TYPES = INTEGER_TYPES + (QVariant.Double, QVariant.String)


def selected_project_layers():
    # Read the actual tree selection, including descendants of selected groups.
    from qgis import utils
    if utils.iface is None:
        return []
    return utils.iface.layerTreeView().selectedLayersRecursive()


class HighlightedLayersWidgetWrapper(MultipleLayerWidgetWrapper):
    """Capture actual layer IDs in the form, before Processing clones the algorithm."""

    def createWidget(self):
        panel = super().createWidget()
        if self.dialogType != DIALOG_STANDARD:
            return panel
        self._panel = panel
        self._options = QgsProcessingUtils.compatibleVectorLayers(QgsProject.instance(), [], False)
        self._option_ids = [layer.id() for layer in self._options]
        self.highlight_button = QPushButton("Use highlighted layers/groups", panel)
        self.highlight_button.setObjectName("useHighlightedLayers")
        self.highlight_button.setToolTip("Replace the input list with the currently highlighted layers and group contents.")
        self.highlight_button.clicked.connect(self.use_highlighted_layers)
        panel.layout().addWidget(self.highlight_button)
        panel.selectionChanged.connect(self.selection_changed)
        self.use_highlighted_layers()
        return panel

    def value(self):
        if self.dialogType != DIALOG_STANDARD:
            return super().value()
        # The choice dialog uses row indices. Resolve them against the same
        # option list shown in that dialog, never a freshly sorted project list.
        return [self._option_ids[item] if isinstance(item, int) else item
                for item in self._panel.selectedoptions]

    def setValue(self, value):
        if self.dialogType != DIALOG_STANDARD:
            return super().setValue(value)
        values = [] if value is None else value if isinstance(value, (list, tuple)) else [value]
        selected = []
        for item in values:
            identifier = item.id() if isinstance(item, QgsVectorLayer) else item
            index = next((i for i, layer in enumerate(self._options)
                          if identifier in (layer.id(), layer.source())), None)
            selected.append(index if index is not None else identifier)
        self._panel.setSelectedItems(selected)
        self.selection_changed()

    def selection_changed(self):
        names = []
        for value in self.value():
            layer = QgsProject.instance().mapLayer(value) if isinstance(value, str) else None
            names.append(layer.name() if layer is not None else str(value))
        self._panel.leText.setToolTip("\n".join(names) if names else
                                     "No layers loaded. Highlight layers/groups and click Use highlighted layers/groups.")
        self.widgetValueHasChanged.emit(self)

    def refresh(self):
        if self.dialogType != DIALOG_STANDARD:
            return super().refresh()
        values = self.value()
        super().refresh()
        self._options = QgsProcessingUtils.compatibleVectorLayers(QgsProject.instance(), [], False)
        self._option_ids = [layer.id() for layer in self._options]
        self.setValue(values)

    def use_highlighted_layers(self):
        # Refresh first, since project layers may have been added after opening.
        if hasattr(self, "widget"):
            self.refresh()
        layers = [layer for layer in selected_project_layers()
                  if isinstance(layer, QgsVectorLayer) and layer.isValid()]
        self.setValue(list({layer.id(): layer for layer in layers}.values()))


def z_index_field(layer, override):
    fields = layer.fields()
    if override:
        return next((i for i, field in enumerate(fields) if field.name() == override), -1)
    exact = fields.indexFromName("z_index")
    if exact >= 0:
        return exact
    matches = [i for i, field in enumerate(fields)
               if re.sub(r"[^a-z0-9]", "", field.name().lower()) == "zindex"]
    if len(matches) > 1:
        raise QgsProcessingException(layer.name() + ": several z-index fields found; set the field override in Advanced.")
    return matches[0] if matches else -1


def stored_value(field, value):
    if field.type() not in SUPPORTED_TYPES:
        raise QgsProcessingException("Unsupported z-index field type: " + field.typeName())
    if field.type() in (QVariant.UInt, QVariant.ULongLong) and value < 0:
        raise QgsProcessingException("A negative value cannot be stored in an unsigned z-index field.")
    return str(value) if field.type() == QVariant.String else value


class ModifyZIndex(QgsProcessingAlgorithm):
    def name(self):
        return "modify_cofrance_z_index"

    def displayName(self):
        return "Modify CoFrance Z-index"

    def group(self):
        return "CoFrance"

    def groupId(self):
        return "cofrance"

    def createInstance(self):
        return ModifyZIndex()

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def shortHelpString(self):
        return (
            "Set one integer z-index value across chosen layers. The dialog loads the highlighted vector layers/groups "
            "into its Layers input when opened. Click Use highlighted layers/groups to refresh the targets after changing "
            "the tree selection, or use the normal layer picker. Check the selected layer count before running. "
            "Default scope: every feature visible through each layer's filter. Optional scope: selected features only; "
            "layers with no selected features are skipped. The existing z_index field is used automatically; "
            "z-index/zIndex spellings are also recognised. Layers without the field are skipped and reported by default. "
            "No field is created. Expand Advanced for an exact field-name override or to keep undoable changes in QGIS. "
            "By default, changes are saved to each source file; save/close any existing edit session first. "
            "Review mode preserves existing edits and adds an undoable edit command, without saving. "
            "Only the z-index attribute is changed; geometry, zoom fields and other attributes are retained. "
            "Completed source saves are per layer. If a later save fails or you cancel, earlier saved layers stay updated. "
            "The exporter reads this attribute as z-index; this tool does not reorder the Layers panel."
        )

    def initAlgorithm(self, config=None):
        layers = QgsProcessingParameterMultipleLayers(
            "LAYERS", "Layers to update (loaded from highlighted layers/groups)",
            layerType=QgsProcessing.TypeVector, optional=True)
        layers.setMetadata({"widget_wrapper": {"class": HighlightedLayersWidgetWrapper}})
        self.addParameter(layers)
        self.addParameter(QgsProcessingParameterNumber(
            "VALUE", "New Z-index", QgsProcessingParameterNumber.Integer,
            defaultValue=0, minValue=-2147483648, maxValue=2147483647))
        self.addParameter(QgsProcessingParameterEnum(
            "SCOPE", "Features to update", ["All features in each chosen layer", "Selected map features only (not highlighted layers)"], defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            "SAVE_MODE", "Save changes", ["Save directly to source files", "Keep undoable edits in QGIS"], defaultValue=0))
        self.addParameter(QgsProcessingParameterString(
            "FIELD", "Exact field name override (empty = detect z_index)", defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            "SKIP_MISSING", "Skip layers without a Z-index field", defaultValue=True))
        for key in ("SAVE_MODE", "FIELD", "SKIP_MISSING"):
            parameter = self.parameterDefinition(key)
            parameter.setFlags(parameter.flags() | parameter.FlagAdvanced)
        for key, label in (("LAYERS_UPDATED", "Layers updated"), ("FEATURES_UPDATED", "Features updated"),
                           ("LAYERS_SKIPPED", "Layers skipped")):
            self.addOutput(QgsProcessingOutputNumber(key, label))
        self.addOutput(QgsProcessingOutputString("REPORT", "Per-layer update report (JSON)"))

    def processAlgorithm(self, parameters, context, feedback):
        number = self.parameterAsDouble(parameters, "VALUE", context)
        if not math.isfinite(number) or not number.is_integer() or not -2147483648 <= number <= 2147483647:
            raise QgsProcessingException("Z-index must be a whole number between -2147483648 and 2147483647.")
        value = int(number)
        layers = self.parameterAsLayerList(parameters, "LAYERS", context) or selected_project_layers()
        if not layers:
            raise QgsProcessingException("Select layers/groups in the Layers panel, or choose layers in this tool.")
        # Remove repeated project layer IDs, e.g. selecting a group and one child.
        layers = list({layer.id(): layer for layer in layers}.values())
        feedback.pushInfo("Target layers: " + ", ".join(layer.name() for layer in layers))
        selected_only = self.parameterAsEnum(parameters, "SCOPE", context) == 1
        save = self.parameterAsEnum(parameters, "SAVE_MODE", context) == 0
        override = self.parameterAsString(parameters, "FIELD", context).strip()
        skip_missing = self.parameterAsBool(parameters, "SKIP_MISSING", context)
        plans, report = [], []

        # Preflight every layer before changing any attributes.
        for layer in layers:
            entry = {"layer": layer.name(), "status": "skipped", "updated": 0}
            if not isinstance(layer, QgsVectorLayer):
                entry["reason"] = "Not a vector layer"
            elif not layer.isValid():
                raise QgsProcessingException("Invalid layer: " + layer.name())
            elif selected_only and not layer.selectedFeatureCount():
                entry["reason"] = "No selected features"
            else:
                index = z_index_field(layer, override)
                if index < 0:
                    if not skip_missing:
                        raise QgsProcessingException(layer.name() + ": missing " + (override or "z_index") + " field. No layers changed.")
                    entry["reason"] = "Missing " + (override or "z_index") + " field"
                else:
                    field = layer.fields()[index]
                    if layer.fields().fieldOrigin(index) != QgsFields.OriginProvider:
                        raise QgsProcessingException(layer.name() + ": z-index must be a stored field, not a joined or expression field.")
                    target = stored_value(field, value)
                    if layer.readOnly() or not layer.dataProvider().capabilities() & QgsVectorDataProvider.ChangeAttributeValues:
                        raise QgsProcessingException(layer.name() + ": source does not support attribute edits.")
                    if save and layer.isEditable():
                        raise QgsProcessingException(layer.name() + ": already in edit mode. Save/close edits first, or choose Keep undoable edits in QGIS in Advanced. No layers changed.")
                    entry.update(field=field.name(), status="ready")
                    plans.append((layer, index, target, entry))
            report.append(entry)
            if entry["status"] == "skipped":
                feedback.pushWarning(layer.name() + ": " + entry["reason"] + "; skipped.")
        if not plans:
            raise QgsProcessingException("No editable layers with a Z-index field match the chosen scope. No layers changed.")
        updated_layers = updated_features = 0
        for position, (layer, index, target, entry) in enumerate(plans):
            if feedback.isCanceled():
                raise QgsProcessingException("Canceled. Earlier completed layers retain their changes.")
            request = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry).setSubsetOfAttributes([index])
            owned = not layer.isEditable()
            if owned and not layer.startEditing():
                raise QgsProcessingException("Could not start editing " + layer.name())
            # Starting an OGR edit session can reopen the provider connection.
            # Create the iterator afterward so it reads from the current connection.
            features = layer.getSelectedFeatures(request) if selected_only else layer.getFeatures(request)
            layer.beginEditCommand("Set Z-index to " + str(value))
            command_active = True
            changed = 0
            try:
                for feature in features:
                    if feedback.isCanceled():
                        raise QgsProcessingException("Canceled while editing " + layer.name())
                    previous = feature[index]
                    if previous == target:
                        continue
                    # Avoid recalculating unrelated fields with apply-on-update defaults.
                    if not layer.changeAttributeValue(feature.id(), index, target, previous, True):
                        raise QgsProcessingException("Could not change feature {} in {}.".format(feature.id(), layer.name()))
                    changed += 1
                if feedback.isCanceled():
                    raise QgsProcessingException("Canceled while editing " + layer.name())
                layer.endEditCommand()
                command_active = False
                if save:
                    if changed:
                        if not layer.commitChanges():
                            raise QgsProcessingException("Could not save {}: {}".format(layer.name(), "; ".join(layer.commitErrors())))
                    elif owned:
                        layer.rollBack()
                entry.update(status=("saved" if save else "pending_edits") if changed else "unchanged", updated=changed)
            except Exception:
                if command_active:
                    layer.destroyEditCommand()
                if owned and layer.isEditable():
                    layer.rollBack()
                raise
            if changed:
                updated_layers += 1
                updated_features += changed
                layer.triggerRepaint()
            elif owned and layer.isEditable():
                layer.rollBack()
            feedback.pushInfo("{}: {} feature(s) updated in {} ({}).".format(layer.name(), changed, entry["field"], entry["status"]))
            feedback.setProgress(100 * (position + 1) / len(plans))
        feedback.pushInfo("Updated {} feature(s) across {} layer(s).".format(updated_features, updated_layers))
        return {"LAYERS_UPDATED": updated_layers, "FEATURES_UPDATED": updated_features,
                "LAYERS_SKIPPED": sum(entry["status"] == "skipped" for entry in report),
                "REPORT": json.dumps(report, ensure_ascii=False)}
