"""Layer-selection dialog for CoFrance v2 export."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QCheckBox,
    QPushButton,
    QDialogButtonBox,
    QGroupBox,
)
from qgis.core import QgsProject, QgsLayerTreeGroup, QgsLayerTreeLayer, QgsVectorLayer

from .cofrance_schema import CoFranceSchemaError, detect_layer_kind, missing_fields
from .export_plan import plan_group_exports


class GeoJSONStylerDialog(QDialog):
    """Dialog for choosing grouped QGIS layers and export options."""

    def __init__(self, parent=None, iface=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("Export CoFrance v2 GeoJSON")
        self.resize(520, 620)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Highlighted QGIS layers/groups are checked automatically. Adjust the "
            "checkboxes to choose what to export. Output uses WGS 84 (EPSG:4326)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        highlight_row = QHBoxLayout()
        self.use_highlighted_button = QPushButton("Use highlighted layers/groups")
        self.use_highlighted_button.setToolTip(
            "Replace the checked export targets with the current QGIS layer-tree highlights.")
        self.use_highlighted_button.clicked.connect(self.select_highlighted_layers)
        highlight_row.addWidget(self.use_highlighted_button)
        highlight_row.addStretch(1)
        self.selection_summary = QLabel("0 layers selected")
        highlight_row.addWidget(self.selection_summary)
        layout.addLayout(highlight_row)

        self.layer_tree = QTreeWidget()
        self.layer_tree.setHeaderLabels(["Groups and layers", "CoFrance type"])
        self.layer_tree.setColumnWidth(0, 360)
        self.layer_tree.setSelectionMode(QTreeWidget.NoSelection)
        self.layer_tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.layer_tree, stretch=1)

        selection_buttons = QHBoxLayout()
        self.select_all_button = QPushButton("Select all")
        self.select_none_button = QPushButton("Select none")
        self.expand_all_button = QPushButton("Expand all")
        self.collapse_all_button = QPushButton("Collapse all")
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(Qt.Checked))
        self.select_none_button.clicked.connect(lambda: self._set_all_checked(Qt.Unchecked))
        self.expand_all_button.clicked.connect(self.layer_tree.expandAll)
        self.collapse_all_button.clicked.connect(self.layer_tree.collapseAll)
        selection_buttons.addWidget(self.select_all_button)
        selection_buttons.addWidget(self.select_none_button)
        selection_buttons.addStretch(1)
        selection_buttons.addWidget(self.expand_all_button)
        selection_buttons.addWidget(self.collapse_all_button)
        layout.addLayout(selection_buttons)

        options_group = QGroupBox("Export options")
        options_layout = QVBoxLayout(options_group)

        self.batch_export_checkbox = QCheckBox("Export separate files by subgroup")
        self.batch_export_checkbox.setChecked(True)
        options_layout.addWidget(self.batch_export_checkbox)
        batch_help = QLabel(
            "Select a parent group (for example Limoges) to export its numbered "
            "subgroups into one folder. VFR point names export separately; "
            "ARP symbols and ARP names are combined. Uncheck for a single file."
        )
        batch_help.setWordWrap(True)
        options_layout.addWidget(batch_help)

        self.update_manifest_checkbox = QCheckBox("Update manifest.json when found")
        self.update_manifest_checkbox.setChecked(True)
        self.update_manifest_checkbox.setToolTip(
            "Look in the export folder and its parent folders. Add exported map "
            "names to the matching FIR/airport entry, preserving existing maps."
        )
        self.batch_export_checkbox.toggled.connect(self.update_manifest_checkbox.setEnabled)
        options_layout.addWidget(self.update_manifest_checkbox)

        self.use_layer_style_checkbox = QCheckBox("Use QGIS layer styles")
        self.use_layer_style_checkbox.setChecked(True)
        options_layout.addWidget(self.use_layer_style_checkbox)

        layout.addWidget(options_group)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def populate_layers(self, use_highlights=True):
        """Populate the tree using the actual QGIS layer-tree hierarchy."""
        self.layer_tree.blockSignals(True)
        self.layer_tree.clear()

        root = QgsProject.instance().layerTreeRoot()
        self._add_tree_children(self.layer_tree.invisibleRootItem(), root)

        self.layer_tree.expandAll()
        self.layer_tree.blockSignals(False)
        if use_highlights:
            self.select_highlighted_layers(rebuild=False)
        else:
            self._update_selection_summary()

    def _add_tree_children(self, parent_item, tree_node, node_path=()):
        for row, child in enumerate(tree_node.children()):
            child_path = node_path + (row,)
            if isinstance(child, QgsLayerTreeGroup):
                group_item = QTreeWidgetItem(parent_item, [child.name(), ""])
                group_item.setFlags(
                    group_item.flags()
                    | Qt.ItemIsUserCheckable
                    | Qt.ItemIsTristate
                )
                group_item.setCheckState(0, Qt.Unchecked)
                group_item.setData(0, Qt.UserRole, None)
                group_item.setExpanded(True)
                self._add_tree_children(group_item, child, child_path)

                # Hide empty groups that contain no vector layers after recursion.
                if group_item.childCount() == 0:
                    parent_item.removeChild(group_item)

            elif isinstance(child, QgsLayerTreeLayer):
                layer = child.layer()
                if not isinstance(layer, QgsVectorLayer):
                    continue

                field_names = [field.name() for field in layer.fields()]
                geometry_family = self._geometry_family(layer)
                try:
                    layer_kind = detect_layer_kind(geometry_family, field_names)
                    absent = missing_fields(layer_kind, field_names)
                    type_label = layer_kind.title()
                    if absent:
                        type_label += " (missing fields)"
                except CoFranceSchemaError:
                    type_label = "Unsupported"

                layer_item = QTreeWidgetItem(parent_item, [layer.name(), type_label])
                layer_item.setFlags(layer_item.flags() | Qt.ItemIsUserCheckable)
                layer_item.setCheckState(0, Qt.Unchecked)
                layer_item.setData(0, Qt.UserRole, layer)
                # A layer can appear in more than one group. Match the actual
                # tree occurrence rather than selecting every matching layer ID.
                layer_item.setData(0, Qt.UserRole + 1, "/".join(map(str, child_path)))
                layer_item.setToolTip(0, layer.source())

    @staticmethod
    def _geometry_family(layer):
        """Return the generic geometry family used by schema detection."""
        from qgis.core import QgsWkbTypes

        geometry_type = layer.geometryType()
        if geometry_type == QgsWkbTypes.PolygonGeometry:
            return "polygon"
        if geometry_type == QgsWkbTypes.LineGeometry:
            return "line"
        if geometry_type == QgsWkbTypes.PointGeometry:
            return "point"
        return "unknown"

    def _on_item_changed(self, item, column):
        """
        Keep manually checked groups synchronized with their children.

        Qt.ItemIsTristate handles most of this automatically, but this explicit
        pass keeps behavior reliable across QGIS/PyQt versions.
        """
        if column != 0:
            return
        if item.childCount():
            self.layer_tree.blockSignals(True)
            state = item.checkState(0)
            if state in (Qt.Checked, Qt.Unchecked):
                self._set_children_checked(item, state)
            self.layer_tree.blockSignals(False)
        self._update_selection_summary()

    def _set_children_checked(self, item, state):
        for i in range(item.childCount()):
            child = item.child(i)
            child.setCheckState(0, state)
            self._set_children_checked(child, state)

    def _set_all_checked(self, state):
        self.layer_tree.blockSignals(True)
        root = self.layer_tree.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            child.setCheckState(0, state)
            self._set_children_checked(child, state)
        self.layer_tree.blockSignals(False)
        self._update_selection_summary()

    @staticmethod
    def _node_path(node):
        rows = []
        while node.parent() is not None:
            parent = node.parent()
            rows.append(parent.children().index(node))
            node = parent
        return "/".join(map(str, reversed(rows)))

    def select_highlighted_layers(self, checked=False, rebuild=True):
        """Snapshot highlighted nodes into export checkboxes, including group contents."""
        if rebuild:
            # Rebuild so additions/removals since opening cannot select the wrong
            # row or leave stale export-layer pointers in the dialog.
            self.populate_layers(use_highlights=False)
        interface = self.iface
        if interface is None:
            from qgis.utils import iface
            interface = iface
        paths = set()
        if interface is not None:
            view = interface.layerTreeView()
            for node in view.selectedNodes(skipInternal=True):
                descendants = node.findLayers() if isinstance(node, QgsLayerTreeGroup) else [node]
                for child in descendants:
                    if isinstance(child, QgsLayerTreeLayer) and isinstance(child.layer(), QgsVectorLayer):
                        paths.add(self._node_path(child))

        def apply(item):
            layer = item.data(0, Qt.UserRole)
            if layer is not None:
                item.setCheckState(0, Qt.Checked if item.data(0, Qt.UserRole + 1) in paths else Qt.Unchecked)
            else:
                for row in range(item.childCount()):
                    apply(item.child(row))
        blocked = self.layer_tree.blockSignals(True)
        try:
            apply(self.layer_tree.invisibleRootItem())
        finally:
            self.layer_tree.blockSignals(blocked)
        self._update_selection_summary()

    def _update_selection_summary(self):
        layers = self.get_selected_layers()
        self.selection_summary.setText("{} layer{} selected".format(len(layers), "" if len(layers) == 1 else "s"))
        self.selection_summary.setToolTip("\n".join(layer.name() for layer in layers) if layers else
                                         "Highlight QGIS layers/groups, or check layers in the export tree.")

    def get_selected_layers(self):
        """Return selected layers in bottom-to-top QGIS draw order."""
        return [layer for layer, _ in self.get_selected_layer_entries()]

    def get_selected_layer_entries(self):
        """Keep group ancestry with selected layers for subgroup batching."""
        selected_entries = []
        root = self.layer_tree.invisibleRootItem()

        def collect_in_export_order(item, group_path):
            # QGIS stores/displays layers top-to-bottom, but draw/export order is
            # normally bottom-to-top. Reverse only when collecting for export so
            # the dialog still visually matches the QGIS Layers panel.
            for i in reversed(range(item.childCount())):
                child = item.child(i)
                layer = child.data(0, Qt.UserRole)
                if layer is not None and child.checkState(0) == Qt.Checked:
                    selected_entries.append((layer, group_path))
                if layer is None:
                    collect_in_export_order(child, group_path + (child.text(0),))

        collect_in_export_order(root, ())
        return selected_entries

    def is_batch_export(self):
        return self.batch_export_checkbox.isChecked()

    def get_export_batches(self):
        return plan_group_exports(self.get_selected_layer_entries())

    def should_update_manifest(self):
        return self.update_manifest_checkbox.isChecked()

    def get_style_options(self):
        """Return options consumed by GeoJSONStyler.merge_to_geojson()."""
        return {
            "use_layer_style": self.use_layer_style_checkbox.isChecked(),
        }
