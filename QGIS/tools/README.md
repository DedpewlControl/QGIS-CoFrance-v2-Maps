# CoFrance QGIS Processing tools

All tools target QGIS 3.40.4+. Navigation files and empty templates stay in this
repository; the installer copies the entry scripts into your active profile.

## Update the entire suite

Open **Plugins > Python Console** in QGIS and paste this as **one single line**:

```python
exec(open(r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS\tools\install_cofrance_tools.py", encoding="utf-8-sig").read()); install_cofrance_tools()
```

This installs/updates all thirteen tools, refreshes Processing, and backs up earlier
copies under your profile's processing/cofrance_script_backups directory.
Backups are outside the Processing scripts folder so QGIS cannot load duplicate
algorithms from them. The recognized old Bordeaux script, combined navigation entry and separate
folder names entry are archived.

For a preview without changes, call install_cofrance_tools(dry_run=True).
Alternatively use **Processing Toolbox > Scripts > Add Script to Toolbox** for
each Python file below. Shared helpers are loaded from QGIS/tools automatically.

## Tools

| Toolbox name | Script | Function |
|---|---|---|
| Generate CoFrance navaid packages | [generate_navaid_packages.py](generate_navaid_packages.py) | Perimeter/list selection; external navaid-type database check; Symbols + Names |
| Generate CoFrance airport packages | [generate_airport_packages.py](generate_airport_packages.py) | Perimeter/ICAO selection; SIA AD 2 runway surface symbols + Names |
| Generate CoFrance runway packages | [generate_runway_packages.py](generate_runway_packages.py) | Perimeter/ICAO/runway-end selection; Lines only |
| Generate CoFrance airway packages | [generate_airways.py](generate_airways.py) | High/low airways by identifier list or perimeter; Lines + Symbols + Names |
| Generate CoFrance SID packages | [generate_sid.py](generate_sid.py) | SID reference/manual routes by ICAO and runways; Lines + Symbols + Names |
| Generate CoFrance STAR packages | [generate_star.py](generate_star.py) | STAR reference/manual routes by ICAO and runways; Lines + Symbols + Names |
| Generate CoFrance trajectory points and names | [generate_trajectory_points.py](generate_trajectory_points.py) | Named points on any selected trajectory layer; Symbols + Names |
| Generate CoFrance point names | [generate_fix_names.py](generate_fix_names.py) | Manual: one symbol layer; Automatic: scan a folder recursively; detailed settings under Advanced |
| Create CoFrance points from list | [create_points_from_list.py](create_points_from_list.py) | Symbols + Names from specified identifiers |
| Generate CoFrance holding pattern | [generate_holding.py](generate_holding.py) | Terminal/enroute holding racetrack and flight-level metadata |
| Create CoFrance map structure | [create_map_structure.py](create_map_structure.py) | Approach CCA layout with AMG/AMSR/MVA; Enroute/Aerodrome roots prepared for later layouts |
| Import exported CoFrance map | [import_cofrance_map.py](import_cofrance_map.py) | Recover editable packages/styles from exported map GeoJSON |
| Modify CoFrance Z-index | [modify_z_index.py](modify_z_index.py) | Set the Z-index field across highlighted layers/groups or an explicit layer list |

The navaid tool now checks types against the selected SIA eAIP edition by
default. It downloads and caches the reference for 24 hours, matches identifiers,
positions and available frequencies, records the AIRAC cycle, and distinguishes DME, VOR-DME, NDB-DME,
TACAN and VORTAC within the mixed source layers. See the navaid guide for cache,
matching and local typed-database options. The navaid tool also checks nearby
airport AD 2.19 pages automatically for unresolved points. Unverified entries
are skipped and listed in the report by default; strict stop mode remains in
Advanced. Older QGIS dialogs may remember the previous strict setting.

Airport symbols default to SIA AD 2.12 runway surfaces: any paved runway uses
aerodrome_paved_ticks; airports with only unpaved/grass runways use aerodrome_ticks.
The tool caches the selected edition's airport pages and retains a manual symbol
override. See the airport guide for offline pages and unavailable AD 2 entries.

## Output naming

GeoPackage filenames and actual layer names match. Map/layer names are separate
from the semantic text category. New name features use FIX_NAME, VOR_NAME,
NDB_NAME, DME_NAME, TACAN_NAME, VORTAC_NAME or ARP_NAME according to point type.
Mixed names packages can therefore contain several uuid categories.

The point name generator has a Manual/Automatic mode selector. Manual uses a
searchable symbol layer and output GeoPackage; Automatic uses a regional folder
and its subfolders. Fill only the inputs for the selected mode. Point-type and
category overrides, offsets, CRS and tolerance are in Advanced. Blank category
means automatic. Legacy IFR_FIX_Names and IFR_FIX_NAMS values are migrated to
automatic categories. Automatic mode always generates semantic categories.

Imported GeoJSON preserves its original uuid values, since import is a recovery
operation. Existing saved GeoPackages are not rewritten by updating these scripts.

Labels retain Arial, Single Labels on text, Offset from point placement, and
the adjustable default 3500-metre northward geometry shift in EPSG:2154.

## Guides

- [Navaid packages](generate_navaid_packages.md)
- [Airport packages](generate_airport_packages.md)
- [Runway packages](generate_runway_packages.md)
- [Airway packages](generate_airways.md)
- [SID packages](generate_sid.md)
- [STAR packages](generate_star.md)
- [Shared navigation extraction details](generate_navigation_packages.md)
- [Generic trajectory symbols and names](generate_trajectory_points.md)
- [Point names: manual or automatic](generate_fix_names.md)
- [Specified point lists](create_points_from_list.md)
- [Holding patterns](generate_holding.md)
- [Map structure](create_map_structure.md)
- [Import exported maps](import_cofrance_map.md)
- [Modify Z-index](modify_z_index.md)

The STAR reference has grouped approach labels, not individual STAR identifiers.
Use its airport/runway/ILS/RNAV groups, an alternative detailed reference, or
Manual STAR. See the navigation-package guide before selecting those filters.

The shared cofrance_tool_dialogs.py, generate_navigation_packages.py and
cofrance_reference_packages.py, navaid_type_lookup.py, sia_navaid_reference.py and
sia_airport_reference.py stay in
the repository and are not installed as toolbox entries. Use the installer rather
than adding the entire tools directory as a scripts folder.

The old generate_bordeaux_fra_points.py source file is retained as a compatibility
entry point. Install the generic trajectory script for current work.

