"""Focused CoFrance reference extraction Processing entry point."""
import importlib.util
from pathlib import Path
_path = Path(r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS\tools\cofrance_reference_packages.py")
_spec = importlib.util.spec_from_file_location("_cofrance_reference_packages", _path)
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)


class GenerateRunwayPackages(_shared.RunwayPackages):
    pass
