"""Create matching CoFrance map folders and QGIS Layers-panel groups.

Install with Processing Toolbox > Scripts > Add Script to Toolbox.
"""

from pathlib import Path
import re

from qgis.core import (
    QgsLayerTreeGroup, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingOutputFolder, QgsProcessingOutputNumber, QgsProcessingOutputString,
    QgsProcessingParameterBoolean, QgsProcessingParameterEnum,
    QgsProcessingParameterFile, QgsProcessingParameterString,
)

# Absolute because QGIS copies installed Processing scripts into its profile.
SOURCE_ROOT = Path(
    r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS\src"
)
MAP_TYPES = ("Aerodrome", "Approach", "Enroute")


FIRS = ("LFBB", "LFEE", "LFFF", "LFMM", "LFRR")
# Altitude types are alphabetical; the optional None choice stays last.
ALTITUDE_TYPES = ("AMG", "AMSR", "MVA", "None")


def map_names(value):
    name = value.strip()
    if (not name or name in (".", "..") or name.endswith((".", " "))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", name.split(".")[0], re.I)):
        raise QgsProcessingException(
            "Enter a map name such as La Rochelle, without path separators or Windows filename characters.")
    return name, "CCA_" + re.sub(r"\s+", "_", name)


def subgroup_names(base, altitude, procedures):
    names = ["0_" + base]
    if altitude != "None":
        names.append("1_" + base + "_" + altitude)
    if procedures:
        names.append("2_" + base + "_Procedures")
    names.extend(("3_" + base + "_Navaids_Names", "3_" + base + "_FIX_Names", "4_" + base + "_VFR"))
    return names


def ensure_group(parent, name, at_top=False):
    for child in parent.children():
        if isinstance(child, QgsLayerTreeGroup) and child.name().casefold() == name.casefold():
            return child, False
    return (parent.insertGroup(0, name) if at_top else parent.addGroup(name)), True


class CreateMapStructure(QgsProcessingAlgorithm):
    def name(self):
        return "create_map_structure"

    def displayName(self):
        return "Create CoFrance map structure"

    def group(self):
        return "CoFrance"

    def groupId(self):
        return "cofrance"

    def createInstance(self):
        return CreateMapStructure()

    def flags(self):
        # Changes to the project's layer tree must run on the QGIS main thread.
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def shortHelpString(self):
        return (
            "Choose Approach, Enroute or Aerodrome. Approach creates the existing full CCA layout. "
            "Enroute and Aerodrome currently create only a map root folder and matching project group; "
            "their detailed layouts will be added later. Altitude/Procedures options apply to Approach only. "
            "Create a map folder and matching groups in the current QGIS Layers panel. "
            "Example: source root/FIR/La Rochelle/0_CCA_La_Rochelle, optional "
            "1_..._AMG, AMSR or MVA and 2_..._Procedures, 3_..._Navaids_Names, "
            "3_..._FIX_Names and 4_..._VFR. QGIS groups go under CCA/FIR - CCA Maps/La Rochelle. "
            "Existing folders and groups are reused; files and layers are not moved or replaced. "
            "No GeoPackages are created. Save the QGIS project afterward to retain its groups."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum(
            "MAP_TYPE", "Map type", options=list(MAP_TYPES), defaultValue=MAP_TYPES.index("Approach")))
        self.addParameter(QgsProcessingParameterString("MAP_NAME", "Map name (e.g. La Rochelle)"))
        self.addParameter(QgsProcessingParameterEnum("FIR", "FIR", options=list(FIRS), defaultValue=0))
        self.addParameter(QgsProcessingParameterFile(
            "SOURCE_ROOT", "Source folder containing the FIR folders",
            behavior=QgsProcessingParameterFile.Folder, defaultValue=str(SOURCE_ROOT)))
        self.addParameter(QgsProcessingParameterEnum(
            "ALTITUDE", "Approach: altitude folder", options=list(ALTITUDE_TYPES), defaultValue=ALTITUDE_TYPES.index("MVA")))
        self.addParameter(QgsProcessingParameterBoolean(
            "PROCEDURES", "Approach: include Procedures folder/group", defaultValue=True))
        self.addOutput(QgsProcessingOutputFolder("MAP_FOLDER", "Map folder"))
        self.addOutput(QgsProcessingOutputString("GROUP_PATH", "QGIS group path"))
        self.addOutput(QgsProcessingOutputString("LAYOUT_STATUS", "Layout status"))
        self.addOutput(QgsProcessingOutputNumber("FOLDERS_CREATED", "Folders created"))
        self.addOutput(QgsProcessingOutputNumber("GROUPS_CREATED", "QGIS groups created"))

    def processAlgorithm(self, parameters, context, feedback):
        project = context.project()
        if project is None:
            raise QgsProcessingException("Run this tool with an open QGIS project.")
        map_type = MAP_TYPES[self.parameterAsEnum(parameters, "MAP_TYPE", context)]
        name, base = map_names(self.parameterAsString(parameters, "MAP_NAME", context))
        fir = FIRS[self.parameterAsEnum(parameters, "FIR", context)]
        altitude = ALTITUDE_TYPES[self.parameterAsEnum(parameters, "ALTITUDE", context)]
        procedures = self.parameterAsBool(parameters, "PROCEDURES", context)
        root_value = self.parameterAsFile(parameters, "SOURCE_ROOT", context)
        if not root_value:
            raise QgsProcessingException("Choose a source folder containing the FIR folders.")
        source_root = Path(root_value).resolve()
        if not source_root.is_dir():
            raise QgsProcessingException("Select an existing source folder containing the FIR folders.")
        # Approach retains the existing disk paths and CCA project hierarchy.
        # Other types have isolated roots until their detailed layouts are specified.
        approach = map_type == "Approach"
        category_root = source_root / fir if approach else source_root / fir / map_type
        map_folder = category_root / name
        names = subgroup_names(base, altitude, procedures) if approach else []
        paths = list(dict.fromkeys([source_root / fir, category_root, map_folder]
                                   + [map_folder / child for child in names]))
        group_root = "CCA" if approach else map_type
        fir_group_name = fir + " - " + ("CCA" if approach else map_type) + " Maps"
        layout_status = "complete" if approach else "root_only_pending_layout"
        if not approach:
            feedback.pushInfo(map_type + ": creating a map root only. The detailed folder layout is pending; "
                              "Approach altitude and Procedures options do not apply.")
        # Validate every target before making any changes.
        for path in paths:
            if not path.resolve().is_relative_to(source_root):
                raise QgsProcessingException(
                    "A target folder resolves outside the selected source root: " + str(path))
            if path.exists() and not path.is_dir():
                raise QgsProcessingException("A file already occupies a required folder path: " + str(path))
        if feedback.isCanceled():
            return {}
        folders_created = 0
        for path in paths:
            if not path.exists():
                path.mkdir()
                folders_created += 1
                feedback.pushInfo("Created folder: " + str(path))

        cca, created = ensure_group(project.layerTreeRoot(), group_root)
        groups_created = int(created)
        fir_group, created = ensure_group(cca, fir_group_name)
        groups_created += int(created)
        map_group, created = ensure_group(fir_group, name)
        groups_created += int(created)
        # New VFR/text groups appear above base symbols and backgrounds.
        for subgroup in names:
            _, created = ensure_group(map_group, subgroup, at_top=True)
            groups_created += int(created)
        if groups_created:
            project.setDirty(True)
        feedback.pushInfo(
            "Created {} folder(s) and {} QGIS group(s). Save the QGIS project to keep the groups.".format(
                folders_created, groups_created))
        return {
            "MAP_FOLDER": str(map_folder), "GROUP_PATH": group_root + "/" + fir_group_name + "/" + name,
            "LAYOUT_STATUS": layout_status,
            "FOLDERS_CREATED": folders_created, "GROUPS_CREATED": groups_created,
        }
