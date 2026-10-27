"""Compatibility entry point. Install generate_trajectory_points.py for the generic tool."""
import importlib.util
from pathlib import Path

path = Path(r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS\tools\generate_trajectory_points.py")
spec = importlib.util.spec_from_file_location("_cofrance_trajectory_compat", path)
_core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_core)
package_tools = _core.package_tools

class BordeauxTrajectoryPoints(_core.GenerateTrajectoryPoints):
    def name(self):
        return "bordeaux_fra_trajectory_points"

    def createInstance(self):
        return BordeauxTrajectoryPoints()
