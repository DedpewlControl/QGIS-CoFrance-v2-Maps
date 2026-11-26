"""QGIS integration tests for perimeter airway and procedure packages."""

import gc
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest

from qgis.PyQt.QtGui import QFontDatabase
from qgis.core import (
    QgsApplication,QgsCoordinateReferenceSystem,QgsCoordinateTransform,QgsFeature,
    QgsGeometry,QgsPointXY,QgsProcessingContext,QgsProcessingException,
    QgsProcessingFeatureSourceDefinition,QgsProcessingFeedback,QgsProject,QgsVectorLayer,
)

APP=TOOL=EXPORTER=None
DIRECTORIES=[]


def setUpModule():
    global APP,TOOL,EXPORTER
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    APP=QgsApplication.instance() or QgsApplication([],False)
    APP.initQgis()
    sys.path.insert(0,str(Path(QgsApplication.prefixPath())/"python/plugins"))
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/arial.ttf")
    import generate_navigation_packages
    TOOL=generate_navigation_packages
    package=types.ModuleType("_navigation_export_check")
    package.__path__=[str(Path(__file__).resolve().parents[2]/"QGIS-CoFrance-Exporter")]
    sys.modules[package.__name__]=package
    EXPORTER=importlib.import_module(package.__name__+".geojson_styler").GeoJSONStyler


def tearDownModule():
    gc.collect()
    APP.exitQgis()
    for path in DIRECTORIES:
        assert path.resolve().parent==Path(__file__).resolve().parent
        assert path.name.startswith("navigation_test_")
        shutil.rmtree(path)


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix="navigation_test_",dir=Path(__file__).resolve().parent))
        DIRECTORIES.append(self.root)
        self.project=QgsProject()
        self.context=QgsProcessingContext();self.context.setProject(self.project)
        self.algorithm=TOOL.GenerateNavigationPackages();self.algorithm.initAlgorithm()
        self.feedback=QgsProcessingFeedback()
        self.perimeter=self.layer("Perimeter","Polygon",[],[
            ({},QgsGeometry.fromPolygonXY([
                [self.p(0,0),self.p(10000,0),self.p(10000,10000),self.p(0,10000),self.p(0,0)],
                [self.p(4000,4000),self.p(4000,6000),self.p(6000,6000),self.p(6000,4000),self.p(4000,4000)]]))])
        self.points("LFXX_VOR",[("CNA",2000,2000)])
        self.points("LFXX_NDB",[("NDB1",8000,2000)])
        self.points("LFXX_FIX_ENR",[("OUT",-1000,2000),("START",0,2000),("FAR",11000,2000),
                                    ("HOLE",5000,5000),("FIXC",2000,8000)])
        self.points("LFXX_FIX_PROC",[("FIXB",8000,8000)])
        self.high=self.layer("LFXX_HIGH_AWY","MultiLineString",["airway"],[
            ({"airway":"A2"},self.line([(-1000,2000),(11000,2000)])),
            ({"airway":"B1"},self.line([(-1000,5000),(11000,5000)]))])
        self.low=self.layer("LFXX_LOW_AWY","MultiLineString",["airway"],[
            ({"airway":"L1"},self.line([(2000,-1000),(2000,11000)]))])
        self.star=self.layer("LFXX_STAR","MultiLineString",["icao","runway","category","procedures"],[
            ({"icao":"LFBD","runway":"23","category":"STAR","procedures":"ILS23;RNAV23"},
             self.line([(2000,8000),(2000,2000),(8000,2000)])),
            ({"icao":"LFBD","runway":"05","category":"STAR","procedures":"STAR"},
             self.line([(8000,8000),(8000,2000)]))])
        self.sid=self.layer("LFXX_SID","MultiLineString",["icao","runway","sid"],[
            ({"icao":"LFBD","runway":"23","sid":"TEST1A"},
             self.line([(2000,2000),(8000,2000),(8000,8000)])),
            ({"icao":"LFBD","runway":"05","sid":"OTHER2B"},self.line([(2000,2000),(2000,8000)]))])
        self.params={"PERIMETER":self.perimeter,"MODE":0,"AIRWAY_SELECTION":0,"ICAO":"LFBD","RUNWAYS":"23","BASE_NAME":"CCA_Test","WORK_CRS":"EPSG:2154",
                     "CLIP":0,"UNNAMED":False,"TOLERANCE":5,"DX":0,"DY":3500,
                     "OUTPUT_FOLDER":str(self.root/"output")}

    def tearDown(self):
        self.perimeter=self.high=self.low=self.star=self.sid=None
        self.context=None
        self.project.clear();self.project=None
        gc.collect()

    def p(self,x,y):
        return QgsPointXY(700000+x,6600000+y)

    def line(self,points):
        return QgsGeometry.fromMultiPolylineXY([[self.p(*point) for point in points]])

    def layer(self,name,kind,fields,rows):
        uri=kind+"?crs=EPSG:2154"+"".join("&field="+field+":string" for field in fields)
        layer=QgsVectorLayer(uri,name,"memory")
        for values,geometry in rows:
            feature=QgsFeature(layer.fields());feature.setGeometry(geometry)
            for field,value in values.items():
                feature[field]=value
            self.assertTrue(layer.dataProvider().addFeatures([feature])[0])
        self.project.addMapLayer(layer)
        return layer

    def points(self,name,rows):
        return self.layer(name,"Point",["ident"],[
            ({"ident":ident},QgsGeometry.fromPointXY(self.p(x,y))) for ident,x,y in rows])

    def run_tool(self,**overrides):
        params=dict(self.params,**overrides)
        valid,message=self.algorithm.checkParameterValues(params,self.context)
        self.assertTrue(valid,message)
        return self.algorithm.processAlgorithm(params,self.context,self.feedback)

    def load(self,path):
        layer=QgsVectorLayer(path,Path(path).stem,"ogr")
        self.assertTrue(layer.isValid(),path);self.project.addMapLayer(layer)
        return layer

    def work_geometry(self,row,layer):
        geometry=QgsGeometry(row.geometry())
        geometry.transform(QgsCoordinateTransform(layer.crs(),QgsCoordinateReferenceSystem("EPSG:2154"),
                                                   self.context.transformContext()))
        return geometry

    def test_airways_manual_list_needs_no_polygon_and_keeps_full_routes(self):
        result=self.run_tool(AIRWAY_SELECTION=1,AIRWAYS="A2",PERIMETER=None)
        lines=self.load(result["LINES"]);symbols=self.load(result["SYMBOLS"])
        self.assertEqual(lines.featureCount(),1)
        geometry=self.work_geometry(next(lines.getFeatures()),lines)
        self.assertLess(geometry.boundingBox().xMinimum(),700000)
        self.assertGreater(geometry.boundingBox().xMaximum(),710000)
        self.assertEqual({r["ident"] for r in symbols.getFeatures()},{"OUT","START","CNA","NDB1","FAR"})
        report=json.loads(Path(result["REPORT"]).read_text(encoding="utf-8"))
        self.assertEqual(report["selection_method"],"airway_list")
        self.assertIsNone(report["perimeter_wgs84"])
        self.assertFalse(report["clipped"])
        with self.assertRaisesRegex(QgsProcessingException,"at least one airway"):
            self.run_tool(AIRWAY_SELECTION=1,AIRWAYS="",PERIMETER=None,
                          OUTPUT_FOLDER=str(self.root/"empty_list"))

    def test_airway_perimeter_mode_still_requires_a_polygon(self):
        with self.assertRaisesRegex(QgsProcessingException,"requires a polygon"):
            self.run_tool(PERIMETER=None)

    def test_procedures_use_airport_runways_without_any_polygon(self):
        for mode in (2,3):
            result=self.run_tool(MODE=mode,PERIMETER=None,
                                 OUTPUT_FOLDER=str(self.root/("procedure_"+str(mode))))
            self.assertEqual(result["LINE_COUNT"],1)
            report=json.loads(Path(result["REPORT"]).read_text(encoding="utf-8"))
            self.assertEqual(report["selection_method"],"airport_runway")
            self.assertEqual(report["airport_icaos"],["LFBD"])
            self.assertEqual(report["runway_configuration"],["23"])
            self.assertIsNone(report["perimeter_wgs84"])
        wide=self.layer("Wide SID","MultiLineString",["icao","runway","sid"],[
            ({"icao":"LFBD","runway":"23","sid":"WIDE1A"},self.line([(-2000,2000),(12000,2000)]))])
        result=self.run_tool(MODE=3,REFERENCE=wide,OUTPUT_FOLDER=str(self.root/"wide_sid"))
        lines=self.load(result["LINES"]);symbols=self.load(result["SYMBOLS"])
        geometry=self.work_geometry(next(lines.getFeatures()),lines)
        self.assertLess(geometry.boundingBox().xMinimum(),700000)
        self.assertIn("OUT",{r["ident"] for r in symbols.getFeatures()})
        self.assertIn("FAR",{r["ident"] for r in symbols.getFeatures()})

    def test_star_parallel_runways_and_runway_independent_groups(self):
        star=self.layer("Parallel STAR reference","MultiLineString",["icao","runway","procedures"],[
            ({"icao":"LFBO","runway":"14","procedures":"ILS14L;RNAV14R"},
             self.line([(2000,2000),(8000,2000)])),
            ({"icao":"LFBO","runway":"","procedures":"STAR"},
             self.line([(2000,2000),(2000,8000)]))])
        result=self.run_tool(MODE=2,REFERENCE=star,ICAO="LFBO",RUNWAYS="14L/14R",PERIMETER=None,
                             PROCEDURES="RNP14R")
        self.assertEqual(result["LINE_COUNT"],1)
        result=self.run_tool(MODE=2,REFERENCE=star,ICAO="LFBO",RUNWAYS="14R",PERIMETER=None,
                             PROCEDURES="STAR",OUTPUT_FOLDER=str(self.root/"common_star"))
        lines=self.load(result["LINES"])
        self.assertEqual(lines.featureCount(),1)
        self.assertEqual(next(lines.getFeatures())["procedures"],"STAR")

    def test_procedure_configuration_required_and_manual_metadata(self):
        for mode in (2,3,4,5):
            with self.assertRaisesRegex(QgsProcessingException,"airport ICAO"):
                self.run_tool(MODE=mode,ICAO="",PERIMETER=None)
            with self.assertRaisesRegex(QgsProcessingException,"runway configuration"):
                self.run_tool(MODE=mode,RUNWAYS="",PERIMETER=None)
        for value in ("00","37","NOT_A_RUNWAY"):
            with self.assertRaisesRegex(QgsProcessingException,"runway numbers"):
                self.run_tool(MODE=3,RUNWAYS=value,PERIMETER=None)
        result=self.run_tool(MODE=4,PERIMETER=None,RUNWAYS="14L / 14R",
                             MANUAL_POINTS="CNA > NDB1")
        lines=self.load(result["LINES"]);row=next(lines.getFeatures())
        self.assertEqual(row["icao"],"LFBD")
        self.assertEqual(row["runway"],"14L;14R")
        report=json.loads(Path(result["REPORT"]).read_text(encoding="utf-8"))
        self.assertEqual(report["runway_configuration"],["14L","14R"])

    def test_high_airways_clip_holes_inside_symbols_and_export(self):
        before=[row.geometry().asWkt() for row in self.high.getFeatures()]
        result=self.run_tool()
        lines=self.load(result["LINES"]);symbols=self.load(result["SYMBOLS"]);names=self.load(result["NAMES"])
        self.assertEqual(lines.featureCount(),2)
        self.assertEqual({row["ident"] for row in symbols.getFeatures()},{"CNA","NDB1","START"})
        self.assertEqual({row["uuid"] for row in names.getFeatures()},{"VOR_NAME","NDB_NAME","FIX_NAME"})
        hole=QgsGeometry.fromPointXY(self.p(5000,5000))
        for row in lines.getFeatures():
            geometry=self.work_geometry(row,lines)
            self.assertGreater(geometry.distance(hole),999)
            for point in geometry.vertices():
                self.assertGreaterEqual(point.x(),700000-0.001)
                self.assertLessEqual(point.x(),710000+0.001)
            if row["route_name"]=="B1":
                self.assertEqual(len(geometry.asMultiPolyline()),2)
        self.assertEqual(before,[r.geometry().asWkt() for r in self.high.getFeatures()])
        exporter=EXPORTER.__new__(EXPORTER);exporter.iface=None
        features=exporter.merge_to_geojson([lines,symbols,names])["features"]
        self.assertTrue(any(f["geometry"]["type"]=="MultiLineString" for f in features))
        self.assertEqual(sum("textStyle" in f["properties"] for f in features),3)
        for key in ("LINES","SYMBOLS","NAMES"):
            self.assertTrue(self.context.willLoadLayerOnCompletion(result[key]))

    def test_low_airways_and_filter_and_keep_complete_features(self):
        result=self.run_tool(MODE=1,AIRWAYS="L*")
        lines=self.load(result["LINES"])
        self.assertEqual({r["route_name"] for r in lines.getFeatures()},{"L1"})
        result=self.run_tool(AIRWAYS="A2",CLIP=1,OUTPUT_FOLDER=str(self.root/"whole"))
        lines=self.load(result["LINES"]);symbols=self.load(result["SYMBOLS"])
        geometry=self.work_geometry(next(lines.getFeatures()),lines)
        self.assertLess(geometry.boundingBox().xMinimum(),700000)
        self.assertGreater(geometry.boundingBox().xMaximum(),710000)
        self.assertNotIn("OUT",{r["ident"] for r in symbols.getFeatures()})
        self.assertNotIn("FAR",{r["ident"] for r in symbols.getFeatures()})

    def test_star_approach_groups_rnp_alias_and_individual_star_limitation(self):
        result=self.run_tool(MODE=2,ICAO="lfbd",RUNWAYS="23",PROCEDURES="RNP23")
        lines=self.load(result["LINES"])
        self.assertEqual(lines.featureCount(),1)
        self.assertEqual(next(lines.getFeatures())["procedures"],"ILS23;RNAV23")
        report=json.loads(Path(result["REPORT"]).read_text(encoding="utf-8"))
        self.assertTrue(report["star_grouped_reference"])
        with self.assertRaisesRegex(QgsProcessingException,"individual named STARs"):
            self.run_tool(MODE=2,PROCEDURES="NONEXISTENT1A",OUTPUT_FOLDER=str(self.root/"missing"))

    def test_sid_named_filter_and_manual_star_sid_order(self):
        result=self.run_tool(MODE=3,ICAO="LFBD",RUNWAYS="23",PROCEDURES="TEST*")
        lines=self.load(result["LINES"])
        self.assertEqual(next(lines.getFeatures())["procedures"],"TEST1A")
        for mode in (4,5):
            result=self.run_tool(MODE=mode,MANUAL_POINTS="VOR:CNA > NDB:NDB1 > FIXB",
                                 OUTPUT_FOLDER=str(self.root/("manual"+str(mode))))
            lines=self.load(result["LINES"]);symbols=self.load(result["SYMBOLS"])
            points=self.work_geometry(next(lines.getFeatures()),lines).asMultiPolyline()[0]
            expected=[self.p(2000,2000),self.p(8000,2000),self.p(8000,8000)]
            self.assertEqual(len(points),3)
            for actual,wanted in zip(points,expected):
                self.assertLess(actual.distance(wanted),0.0001)
            self.assertEqual({r["ident"] for r in symbols.getFeatures()},{"CNA","NDB1","FIXB"})

    def test_manual_branches_additional_point_source_and_no_bridging_unknowns(self):
        extra=self.points("My procedure points",[("CUSTOM",2000,9000)])
        result=self.run_tool(MODE=4,POINT_REFERENCE=extra,
                             MANUAL_POINTS="Alpha | CNA > NDB1\nBravo | CNA > CUSTOM")
        lines=self.load(result["LINES"]);symbols=self.load(result["SYMBOLS"])
        self.assertEqual(lines.featureCount(),2)
        self.assertEqual(symbols.featureCount(),3)
        self.assertIn("CUSTOM",{r["ident"] for r in symbols.getFeatures()})
        with self.assertRaisesRegex(QgsProcessingException,"not found"):
            self.run_tool(MODE=5,MANUAL_POINTS="CNA > NO_SUCH_POINT > NDB1",
                          OUTPUT_FOLDER=str(self.root/"invalid"))
        self.assertFalse((self.root/"invalid").exists())

    def test_selected_perimeter_only_and_no_symbols_in_hole(self):
        second=QgsFeature(self.perimeter.fields())
        second.setGeometry(QgsGeometry.fromPolygonXY([[self.p(10500,1500),self.p(11500,1500),
            self.p(11500,2500),self.p(10500,2500),self.p(10500,1500)]]))
        self.perimeter.dataProvider().addFeatures([second])
        first_id=next(self.perimeter.getFeatures()).id()
        self.perimeter.selectByIds([first_id])
        source=QgsProcessingFeatureSourceDefinition(self.perimeter.id(),selectedFeaturesOnly=True)
        result=self.run_tool(PERIMETER=source)
        symbols=self.load(result["SYMBOLS"])
        self.assertNotIn("FAR",{r["ident"] for r in symbols.getFeatures()})
        self.assertNotIn("HOLE",{r["ident"] for r in symbols.getFeatures()})

    def test_unmatched_placeholders_and_empty_point_packages(self):
        result=self.run_tool(MODE=1,AIRWAYS="L1",UNNAMED=True)
        self.assertGreater(result["UNMATCHED_VERTEX_COUNT"],0)
        symbols=self.load(result["SYMBOLS"])
        self.assertTrue(any(r["ident"].startswith("UNNAMED_") for r in symbols.getFeatures()))
        # A valid line with no named navigation point still creates all three packages.
        remote=self.layer("Remote airway","MultiLineString",["airway"],[
            ({"airway":"X1"},self.line([(200,100),(9000,100)]))])
        result=self.run_tool(REFERENCE=remote,OUTPUT_FOLDER=str(self.root/"empty_points"))
        self.assertEqual(result["LINE_COUNT"],1)
        self.assertEqual(result["POINT_COUNT"],0)
        self.assertEqual(self.load(result["NAMES"]).featureCount(),0)
        self.assertEqual(self.load(result["SYMBOLS"]).featureCount(),0)

    def test_existing_output_cancellation_and_third_package_failure(self):
        output=Path(self.params["OUTPUT_FOLDER"]);output.mkdir()
        marker=output/"keep.txt";marker.write_text("keep")
        with self.assertRaisesRegex(QgsProcessingException,"new or empty"):
            self.run_tool()
        self.assertEqual(marker.read_text(),"keep");marker.unlink()
        tools,_=TOOL.shared_tools();original=tools.configure_style
        def broken(layer,text_layer,symbols,assets):
            if text_layer:
                raise QgsProcessingException("Injected third-package failure")
            return original(layer,text_layer,symbols,assets)
        tools.configure_style=broken
        try:
            with self.assertRaisesRegex(QgsProcessingException,"third-package"):
                self.run_tool()
        finally:
            tools.configure_style=original
        self.assertEqual(list(output.iterdir()),[])
        self.assertEqual(list(self.root.glob(".navigation-packages-*")),[])
        self.feedback.cancel()
        with self.assertRaisesRegex(QgsProcessingException,"Canceled"):
            self.run_tool()


if __name__=="__main__":
    unittest.main()
