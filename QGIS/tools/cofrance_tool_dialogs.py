"""Shared focused Processing dialogs; loaded by the public entry scripts."""

from functools import lru_cache
import importlib.util
from pathlib import Path
from qgis.core import (
    QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
)

TOOL_DIRECTORY = Path(r"F:\[French vACC] Navigation & Operations\GitHub Projects\QGIS-CoFrance-Maps\QGIS\tools")


@lru_cache(maxsize=None)
def engine_module(filename):
    path = TOOL_DIRECTORY / filename
    if not path.is_file():
        raise QgsProcessingException("Missing shared tool: " + str(path))
    spec = importlib.util.spec_from_file_location("_cofrance_dialog_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FocusedTool(QgsProcessingAlgorithm):
    ENGINE_FILE = "generate_navigation_packages.py"
    ENGINE_CLASS = "GenerateNavigationPackages"
    ADVANCED = {"REFERENCE", "POINT_REFERENCE", "CLIP", "UNNAMED", "WORK_CRS", "TOLERANCE", "DX", "DY", "CATEGORY"}

    def group(self):
        return "CoFrance"

    def groupId(self):
        return "cofrance"

    def createInstance(self):
        return type(self)()

    def engine(self):
        algorithm = getattr(engine_module(self.ENGINE_FILE), self.ENGINE_CLASS)()
        algorithm.initAlgorithm()
        return algorithm

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def copy_parameters(self, engine, names):
        for name in names:
            parameter = engine.parameterDefinition(name).clone()
            flags = parameter.flags() & ~QgsProcessingParameterDefinition.FlagHidden
            if name in self.ADVANCED:
                flags |= QgsProcessingParameterDefinition.FlagAdvanced
            parameter.setFlags(flags)
            if name == "BASE_NAME":
                parameter.setDefaultValue(self.DEFAULT_BASE)
            self.addParameter(parameter)

    def copy_outputs(self, engine):
        existing = {output.name() for output in self.outputDefinitions()}
        for output in engine.outputDefinitions():
            if output.name() not in existing and output.name() != "OUTPUT":
                self.addOutput(type(output)(output.name(), output.description()))

    def mapped_parameters(self, parameters, context):
        return {}

    def processAlgorithm(self, parameters, context, feedback):
        engine = self.engine()
        # Only this dialog's parameters can reach the engine. Stale inputs from
        # a previous combined dialog cannot change the selected tool's behavior.
        values = {parameter.name(): parameter.defaultValue() for parameter in engine.parameterDefinitions()}
        values.update({parameter.name(): parameter.defaultValue() for parameter in self.parameterDefinitions()})
        values.update({name: value for name, value in parameters.items()
                       if self.parameterDefinition(name) is not None})
        values.update(self.mapped_parameters(parameters, context))
        return engine.processAlgorithm(values, context, feedback)


class NavigationTool(FocusedTool):
    KIND = ""
    DEFAULT_BASE = "Navigation"

    def name(self):
        return "generate_cofrance_" + self.KIND.lower() + "_packages"

    def displayName(self):
        return "Generate CoFrance " + ("airway" if self.KIND == "AIRWAYS" else self.KIND) + " packages"

    def shortHelpString(self):
        common = ("Creates separate Lines, Symbols and Names GeoPackages in a new or empty folder. "
                  "Expand Advanced for reference overrides, matching tolerance and label offsets (default 3500 m north). ")
        if self.KIND == "AIRWAYS":
            return ("Choose high or low airways, then a polygon perimeter or an airway identifier list. "
                    "Perimeter mode clips to the polygon by default; list mode keeps complete matching routes. " + common)
        return ("Enter airport ICAO and runway configuration, then use the reference or an ordered manual point list. "
                "Optional reference filters accept procedure names or approach labels. " + common +
                ("The bundled STAR reference stores grouped approach labels; it cannot isolate individual STARs or merged parallel-runway geometry. "
                 "Use a detailed alternative reference or manual ordered points for individual routes. " if self.KIND == "STAR" else "") +
                "Manual points are straight cartographic connections, not computed ARINC paths.")

    def initAlgorithm(self, config=None):
        engine = self.engine()
        if self.KIND == "AIRWAYS":
            self.addParameter(QgsProcessingParameterEnum("LEVEL", "Airway level", ["High", "Low"], defaultValue=0))
            names = ["AIRWAY_SELECTION", "AIRWAYS", "PERIMETER", "BASE_NAME", "OUTPUT_FOLDER"]
            advanced = ["REFERENCE", "POINT_REFERENCE", "CLIP"]
        else:
            self.addParameter(QgsProcessingParameterEnum("SOURCE", "Drawing source",
                ["Reference procedures", "Manual ordered points"], defaultValue=0))
            names = ["ICAO", "RUNWAYS", "PROCEDURES", "MANUAL_POINTS", "BASE_NAME", "OUTPUT_FOLDER"]
            advanced = ["REFERENCE", "POINT_REFERENCE"]
        self.copy_parameters(engine, names + advanced + ["UNNAMED", "WORK_CRS", "TOLERANCE", "DX", "DY"])
        if self.KIND != "AIRWAYS":
            for name, label in (("ICAO", "Airport ICAO"), ("RUNWAYS", "Runway configuration (e.g. 23 or 14L/14R)"),
                                ("PROCEDURES", "Reference: procedure names or approach labels (optional)"),
                                ("MANUAL_POINTS", "Manual: ordered points / named branches")):
                self.parameterDefinition(name).setDescription(label)
            for name in ("ICAO", "RUNWAYS"):
                parameter = self.parameterDefinition(name)
                parameter.setFlags(parameter.flags() & ~QgsProcessingParameterDefinition.FlagOptional)
        self.copy_outputs(engine)

    def mapped_parameters(self, parameters, context):
        if self.KIND == "AIRWAYS":
            return {"MODE": self.parameterAsEnum(parameters, "LEVEL", context)}
        manual = self.parameterAsEnum(parameters, "SOURCE", context) == 1
        return {"MODE": (4 if manual else 2) + int(self.KIND == "SID")}


class FolderPointNamesTool(FocusedTool):
    ENGINE_FILE = "generate_fix_names.py"
    ENGINE_CLASS = "GenerateFixNames"

    def name(self):
        return "generate_cofrance_point_names_folder"

    def displayName(self):
        return "Generate CoFrance point names from folder"

    def shortHelpString(self):
        return ("Choose a regional map folder. Recursively finds FIX, navaid and ARP symbol packages and creates missing names packages. "
                "Existing names packages, including empty ones, are preserved. Point types and FIX_NAME/VOR_NAME/NDB_NAME/ARP_NAME categories "
                "are detected automatically. Expand Advanced to adjust matching tolerance, CRS or label offsets.")

    def initAlgorithm(self, config=None):
        engine = self.engine()
        self.copy_parameters(engine, ["ROOT_FOLDER", "WORK_CRS", "TOLERANCE", "DX", "DY"])
        parameter = self.parameterDefinition("ROOT_FOLDER")
        parameter.setDescription("Regional map folder (includes subfolders)")
        parameter.setFlags(parameter.flags() & ~QgsProcessingParameterDefinition.FlagOptional)
        self.copy_outputs(engine)

    def mapped_parameters(self, parameters, context):
        return {"MODE": 1, "MASTER_TYPE": 0, "CATEGORY": ""}
