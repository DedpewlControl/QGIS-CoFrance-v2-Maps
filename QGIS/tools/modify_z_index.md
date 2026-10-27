# Modify CoFrance Z-index

Sets the existing Z-index attribute on features in several layers. Install with
the [suite installer](README.md), then search the Processing toolbox for
**Modify CoFrance Z-index**.

## Quick use

1. Highlight the layers in the QGIS Layers panel (Ctrl/Shift for several), or
   highlight their parent groups to include the layers inside them.
2. Open the tool. **Layers to update** automatically loads those vector layers
   and shows the selected count. Hover over the count to see the layer names.
3. If the dialog was already open when you highlighted the layers, click
   **Use highlighted layers/groups** to replace the target list with the current
   selection. Alternatively, use the normal layer picker beside the count.
4. Enter the **New Z-index**, such as 10. This replaces the current value.
5. Keep **All features in each chosen layer**, or choose **Selected map features
   only** if you have selected individual features on the map.
6. Check the layer count and click **Run**. The default saves to each source.

The target list is captured in the dialog and passed to the operation. Changing
or clearing the tree highlight afterward does not silently change that list;
use the refresh button when you want to load a different selection. Explicit
Python/Processing calls can still pass a LAYERS list; an empty list falls back
to the current Layers-panel selection.

Highlighted layers and selected features are separate concepts. Highlighting a
layer chooses which layer to update; the default updates all its features.
Any existing layer filter is respected. With Selected features only, a layer
with no selected features is skipped.

The field is detected separately for each layer: z_index first, then equivalent
z-index/zIndex spellings. Negative integers and zero are supported. The tool
updates point, line, polygon and other editable vector layers. It skips layers
without the field and reports them; it does not create missing fields. Raster
layers selected through a group are also skipped. Existing values equal to the
new value are left unchanged.

## Advanced settings

- **Save changes:** the default saves directly to source files. Target layers
  must be outside edit mode. If any eligible target is already editing, the
  tool stops before changing any layer; save/close that edit session or use the
  review option below.
- **Keep undoable edits in QGIS:** leaves the changes pending. Each layer gets
  a Set Z-index edit command, which can be undone with Ctrl+Z for that layer.
  Existing edits are preserved. Save layer edits later to write to the source.
- **Exact field name override:** use a different existing field name instead of
  automatic detection. The override applies to every chosen layer.
- **Skip layers without a Z-index field:** enabled by default. Disable it to
  require the field on every selected vector layer before any updates begin.

The existing field must be stored and editable; joined and expression fields
are rejected. Integer/numeric fields and text fields containing integer values
are supported. The tool validates eligible layers before making changes. A
failure while updating attributes rolls back the current layer's edit command.
Saving is per layer: layers completed earlier stay updated if a later layer
fails or the operation is canceled. QGIS lists those completed updates in the log.

Geometry, other attributes, styles and the Layers-panel order are retained.
Apply-on-update defaults on unrelated fields are not recalculated. This changes
the stored attribute that the CoFrance exporter emits as z-index; it does not
move layers in the QGIS tree.

Processing outputs include the number of updated layers/features, skipped
layers, and a per-layer JSON report with detected field names and save status.
