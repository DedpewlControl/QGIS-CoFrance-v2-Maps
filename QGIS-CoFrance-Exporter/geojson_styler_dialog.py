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


class GeoJSONStylerDialog(QDialog):
    """Dialog for choosing grouped QGIS layers and export options."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export CoFrance v2 GeoJSON")
        self.resize(520, 620)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Select template layers to export. The output is always transformed "
            "to WGS 84 (EPSG:4326)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

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

        self.use_layer_style_checkbox = QCheckBox("Use QGIS layer styles")
        self.use_layer_style_checkbox.setChecked(True)
        options_layout.addWidget(self.use_layer_style_checkbox)

        layout.addWidget(options_group)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def populate_layers(self):
        """Populate the tree using the actual QGIS layer-tree hierarchy."""
        self.layer_tree.blockSignals(True)
        self.layer_tree.clear()

        root = QgsProject.instance().layerTreeRoot()
        self._add_tree_children(self.layer_tree.invisibleRootItem(), root)

        self.layer_tree.expandAll()
        self.layer_tree.blockSignals(False)

    def _add_tree_children(self, parent_item, tree_node):
        for child in tree_node.children():
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
                self._add_tree_children(group_item, child)

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
        if column != 0 or item.childCount() == 0:
            return

        self.layer_tree.blockSignals(True)
        state = item.checkState(0)
        if state in (Qt.Checked, Qt.Unchecked):
            self._set_children_checked(item, state)
        self.layer_tree.blockSignals(False)

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

    def get_selected_layers(self):
        """
        Return selected QgsVectorLayer objects in the same top-to-bottom order
        shown in this dialog/QGIS layer tree, including the order inside groups
        and nested groups.
        """
        selected_layers = []
        root = self.layer_tree.invisibleRootItem()

        def collect_in_export_order(item):
            # QGIS stores/displays layers top-to-bottom, but draw/export order is
            # normally bottom-to-top. Reverse only when collecting for export so
            # the dialog still visually matches the QGIS Layers panel.
            for i in reversed(range(item.childCount())):
                child = item.child(i)
                layer = child.data(0, Qt.UserRole)
                if layer is not None and child.checkState(0) == Qt.Checked:
                    selected_layers.append(layer)
                collect_in_export_order(child)

        collect_in_export_order(root)
        return selected_layers

    def get_style_options(self):
        """Return options consumed by GeoJSONStyler.merge_to_geojson()."""
        return {
            "use_layer_style": self.use_layer_style_checkbox.isChecked(),
        }
