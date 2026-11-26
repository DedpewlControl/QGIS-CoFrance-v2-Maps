"""Run in QGIS Python Console, then call install_cofrance_tools().

Copies the supported algorithms into this profile's Processing scripts directory.
Existing copies are backed up outside the scripts directory before replacement.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil

TOOL_DIRECTORY = Path(r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS\tools")
ALGORITHMS = (
    "create_map_structure.py",
    "create_points_from_list.py",
    "generate_fix_names.py",
    "generate_holding.py",
    "generate_trajectory_points.py",
    "generate_airways.py",
    "generate_navaid_packages.py",
    "generate_airport_packages.py",
    "generate_runway_packages.py",
    "generate_sid.py",
    "generate_star.py",
    "import_cofrance_map.py",
    "modify_z_index.py",
)
SUPPORT_FILES = ("cofrance_tool_dialogs.py", "generate_navigation_packages.py", "cofrance_reference_packages.py", "navaid_type_lookup.py", "sia_navaid_reference.py", "sia_airport_reference.py")


def install_cofrance_tools(dry_run=False, profile_directory=None):
    from qgis.core import QgsApplication
    if QgsApplication.instance() is None:
        raise RuntimeError("Run this installer inside QGIS.")
    # Check every source before writing anything in the profile.
    for filename in ALGORITHMS + SUPPORT_FILES:
        path = TOOL_DIRECTORY/filename
        if not path.is_file():
            raise RuntimeError("Missing script: " + str(path))
        compile(path.read_text(encoding="utf-8-sig"),str(path),"exec")
    for filename in ("cofrance_lines.gpkg","cofrance_symbols.gpkg","cofrance_text.gpkg"):
        path = TOOL_DIRECTORY.parent/"templates"/filename
        if not path.is_file():
            raise RuntimeError("Missing template: " + str(path))
    profile = Path(profile_directory or QgsApplication.qgisSettingsDirPath()).resolve()
    scripts = profile/"processing"/"scripts"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = profile/"processing"/"cofrance_script_backups"/stamp
    result = {"scripts_directory":str(scripts),"backup_directory":str(backup),
              "installed":list(ALGORITHMS),"archived_legacy":[],"dry_run":dry_run}
    if dry_run:
        print(json.dumps(result,indent=2))
        return result
    scripts.mkdir(parents=True,exist_ok=True)
    backup.mkdir(parents=True,exist_ok=False)
    # Only fixed, known script filenames are touched.
    for filename in ALGORITHMS:
        destination = scripts/filename
        if destination.exists():
            shutil.copy2(destination,backup/filename)
        temporary = scripts/(filename+".new")
        shutil.copy2(TOOL_DIRECTORY/filename,temporary)
        os.replace(temporary,destination)
    legacy_markers = {
        "generate_bordeaux_fra_points.py": ("bordeaux_fra_trajectory_points", "class BordeauxTrajectoryPoints"),
        "generate_navigation_packages.py": ("class GenerateNavigationPackages",),
        "generate_point_names_folder.py": ("class GeneratePointNamesFolder",),
    }
    for filename, markers in legacy_markers.items():
        legacy = scripts/filename
        if legacy.is_file():
            text = legacy.read_text(encoding="utf-8-sig")
            if any(marker in text for marker in markers):
                shutil.move(str(legacy), str(backup/legacy.name))
                result["archived_legacy"].append(legacy.name)
            else:
                print("Existing legacy-named file was not recognized and was left in place: " + str(legacy))
    (backup/"installation.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    if profile_directory is None:
        provider = QgsApplication.processingRegistry().providerById("script")
        if provider is not None:
            provider.refreshAlgorithms()
        else:
            print("Restart QGIS to load the new Processing scripts.")
    print("Installed "+str(len(ALGORITHMS))+" CoFrance tools into "+str(scripts))
    print("Previous script copies: "+str(backup))
    return result

