"""Focused CoFrance Processing entry point."""
import importlib.util
from pathlib import Path

_path = Path(r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS\tools\cofrance_tool_dialogs.py")
_spec = importlib.util.spec_from_file_location("_cofrance_focused_dialogs", _path)
_dialogs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dialogs)


class GenerateAirways(_dialogs.NavigationTool):
    KIND = "AIRWAYS"
    DEFAULT_BASE = "Airways"
