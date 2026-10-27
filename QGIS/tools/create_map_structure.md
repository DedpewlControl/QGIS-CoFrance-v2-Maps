# Create CoFrance map structure

Creates matching directories on disk and groups in the current QGIS project's
Layers panel. Install `create_map_structure.py` with **Processing Toolbox >
Scripts > Add Script to Toolbox**, then search for **Create CoFrance map
structure**.

Choose **Map type** (Aerodrome, Approach or Enroute), the map name, FIR and source root (normally `QGIS/src`). Select **AMG**, **AMSR**, **MVA**
or **None**, and whether to include **Procedures** for Approach. Click **Run**, then
save the QGIS project with **Ctrl+S**.

Approach is the default and retains the existing CCA layout and paths.

Enroute and Aerodrome are prepared as roots only, pending their detailed layouts:

| Type | Disk root | QGIS group |
|---|---|---|
| Approach | QGIS/src/FIR/Map | CCA / FIR - CCA Maps / Map |
| Enroute | QGIS/src/FIR/Enroute/Map | Enroute / FIR - Enroute Maps / Map |
| Aerodrome | QGIS/src/FIR/Aerodrome/Map | Aerodrome / FIR - Aerodrome Maps / Map |

The two new types create no numbered subfolders. Altitude and Procedures options
apply only to Approach. Existing Enroute/Aerodrome data is not moved or migrated.
The result reports root_only_pending_layout for these two types.

For Approach, the altitude choice creates the matching 1_CCA_<map>_AMG,
1_CCA_<map>_AMSR or 1_CCA_<map>_MVA directory and QGIS group. None omits that
section. The dropdown is alphabetical (AMG, AMSR, MVA), with None last.
MVA remains the default selection.

For **La Rochelle**, **LFBB**, **AMSR**, with Procedures enabled:

| Disk folder under QGIS/src/LFBB/La Rochelle | QGIS subgroup under CCA / LFBB - CCA Maps / La Rochelle |
|---|---|
| 0_CCA_La_Rochelle | 0_CCA_La_Rochelle |
| 1_CCA_La_Rochelle_AMSR | 1_CCA_La_Rochelle_AMSR |
| 2_CCA_La_Rochelle_Procedures | 2_CCA_La_Rochelle_Procedures |
| 3_CCA_La_Rochelle_Navaids_Names | 3_CCA_La_Rochelle_Navaids_Names |
| 3_CCA_La_Rochelle_FIX_Names | 3_CCA_La_Rochelle_FIX_Names |
| 4_CCA_La_Rochelle_VFR | 4_CCA_La_Rochelle_VFR |

The map folder and QGIS parent group keep spaces in the map name; the numbered
names replace spaces with underscores. New subgroups appear with VFR and names
above the base map, following the existing project's drawing order.

Existing exact-name directories and groups are reused. Existing layers, files
and group order are preserved. No GeoPackages are created or copied. If you
change the AMG/AMSR/MVA or Procedures choices on a later run, newly requested folders
and groups are added and existing ones remain.

The structure works with automatic name generation: place FIX and Navaid
symbol files in 0_..., ARPs in 4_..._VFR, then scan the regional map folder with
**Generate CoFrance point names**, using **Automatic - folder and subfolders**. The exporter uses the numbered QGIS groups to
organise output.

If the repository moves, update SOURCE_ROOT in the script or choose the new
source root in the tool. The script changes the currently open project in
memory; it does not save or rewrite the .qgz automatically.
