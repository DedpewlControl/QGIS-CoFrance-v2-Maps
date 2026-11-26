"""Spatial matching and output checks for Bordeaux FRA trajectory points."""

import gc
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFeature, QgsGeometry, QgsPointXY, QgsProcessingContext,
    QgsProcessingException, QgsProcessingFeedback, QgsProject, QgsVectorLayer,
)

APP = TOOL = None
DIRECTORIES = []


def setUpModule():
    global APP, TOOL
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    APP=QgsApplication.instance() or QgsApplication([],False)
    APP.initQgis()
    sys.path.insert(0,str(Path(QgsApplication.prefixPath())/"python/plugins"))
    import generate_trajectory_points
    TOOL=generate_trajectory_points


def tearDownModule():
    gc.collect()
    APP.exitQgis()
    for path in DIRECTORIES:
        assert path.resolve().parent==Path(__file__).resolve().parent
        assert path.name.startswith("bordeaux_fra_test_")
        shutil.rmtree(path)


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix="bordeaux_fra_test_",dir=Path(__file__).resolve().parent))
        DIRECTORIES.append(self.root)
        self.project=QgsProject()
        self.context=QgsProcessingContext()
        self.context.setProject(self.project)
        self.algorithm=TOOL.GenerateTrajectoryPoints()
        self.algorithm.initAlgorithm()
        self.feedback=QgsProcessingFeedback()
        self.routes=self.lines("Routes", [[(0,0),(2000,0),(2000,2000)],[(0,0),(2000,0)]])
        self.lines("Routes_Dashed",[[(2000,2000),(3000,2000)]])
        self.placed=self.points("Placed",[("MID",1000,0,"vor_dme"),("TAC",2000,1500,"tacan"),("DMEX",500,0,"dme")])
        self.points("LFXX_VOR",[("MID",1000,0,None)])
        self.points("LFXX_NDB",[])
        self.points("LFXX_FIX_ENR",[("START",0,0,None),("END",2000,0,None),
                                   ("NEAR",1500,4,None),("OFF",1000,6,None)])
        self.points("LFXX_FIX_PROC",[("END",2000,0,None)])
        self.params={"TRAJECTORIES":self.routes,"PLACED_POINTS":self.placed,"DASHED":True,"USE_PLACED":True,
                     "MASTER_SET":1,"MATCH_MODE":0,"UNNAMED":False,"TOLERANCE":5,"FIX_SYMBOL":0,
                     "BASE_NAME":"Bordeaux_Test","WORK_CRS":"EPSG:2154","DX":0,"DY":3500,
                     "OUTPUT_FOLDER":str(self.root/"output")}

    def tearDown(self):
        self.routes=self.placed=None
        self.context=None
        self.project.clear()
        self.project=None
        gc.collect()

    def point(self,x,y):
        return QgsPointXY(700000+x,6600000+y)

    def lines(self,name,parts):
        layer=QgsVectorLayer("MultiLineString?crs=EPSG:2154",name,"memory")
        for coords in parts:
            feature=QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromMultiPolylineXY([[self.point(*point) for point in coords]]))
            self.assertTrue(layer.dataProvider().addFeatures([feature])[0])
        self.project.addMapLayer(layer)
        return layer

    def points(self,name,rows):
        layer=QgsVectorLayer("Point?crs=EPSG:2154&field=ident:string&field=symbol_type:string",name,"memory")
        for ident,x,y,symbol in rows:
            feature=QgsFeature(layer.fields())
            feature["ident"]=ident
            feature["symbol_type"]=symbol
            feature.setGeometry(QgsGeometry.fromPointXY(self.point(x,y)))
            self.assertTrue(layer.dataProvider().addFeatures([feature])[0])
        self.project.addMapLayer(layer)
        return layer

    def run_tool(self,**overrides):
        params=dict(self.params,**overrides)
        valid,message=self.algorithm.checkParameterValues(params,self.context)
        self.assertTrue(valid,message)
        return self.algorithm.processAlgorithm(params,self.context,self.feedback)

    def load(self,path):
        layer=QgsVectorLayer(path,Path(path).stem,"ogr")
        self.assertTrue(layer.isValid())
        self.project.addMapLayer(layer)
        return layer

    def test_points_on_segments_vertices_dedup_and_placed_symbols(self):
        before={layer.id():[row.geometry().asWkt() for row in layer.getFeatures()]
                for layer in self.project.mapLayers().values()}
        result=self.run_tool()
        self.assertEqual(result["MATCHED_COUNT"],6)
        self.assertEqual(result["LINE_COUNT"],3)
        self.assertEqual(result["VERTEX_COUNT"],4)
        self.assertEqual(result["UNMATCHED_VERTEX_COUNT"],2)
        symbols=self.load(result["SYMBOLS"])
        names=self.load(result["NAMES"])
        rows={row["ident"]:row for row in symbols.getFeatures()}
        self.assertEqual(set(rows),{"START","END","MID","TAC","DMEX","NEAR"})
        self.assertEqual(rows["MID"]["symbol_type"],"vor_dme")
        self.assertEqual(rows["TAC"]["symbol_type"],"tacan")
        self.assertEqual(rows["DMEX"]["symbol_type"],"dme")
        self.assertEqual(names.featureCount(),6)
        work=QgsCoordinateReferenceSystem("EPSG:2154")
        to_work=QgsCoordinateTransform(names.crs(),work,self.context.transformContext())
        for row in names.getFeatures():
            name_point=to_work.transform(row.geometry().asMultiPoint()[0])
            symbol_point=to_work.transform(rows[row["ident"]].geometry().asMultiPoint()[0])
            self.assertAlmostEqual(name_point.y()-symbol_point.y(),3500,places=5)
        for category in symbols.renderer().categories():
            if category.value() in {"vor_dme","tacan","dme"}:
                svg=Path(category.symbol().symbolLayer(0).path())
                self.assertTrue(svg.is_file())
                self.assertTrue(svg.resolve().is_relative_to(Path(self.params["OUTPUT_FOLDER"]).resolve()))
        for layer_id,wkts in before.items():
            self.assertEqual([r.geometry().asWkt() for r in self.project.mapLayer(layer_id).getFeatures()],wkts)
        report=json.loads(Path(result["REPORT"]).read_text(encoding="utf-8"))
        start=next(p for p in report["points"] if p["ident"]=="START")
        self.assertEqual(len(start["routes"]),2)
        self.assertTrue(all(v["nearest_hint_only"] for v in report["unmatched_vertices"]))

    def test_vertices_only_excludes_middle_and_near_segment_points(self):
        result=self.run_tool(MATCH_MODE=1)
        symbols=self.load(result["SYMBOLS"])
        self.assertEqual({r["ident"] for r in symbols.getFeatures()},{"START","END"})
        self.assertEqual(result["MATCHED_COUNT"],2)
        self.assertEqual(result["UNMATCHED_VERTEX_COUNT"],2)

    def test_unnamed_placeholders_cover_each_unmatched_vertex(self):
        result=self.run_tool(UNNAMED=True)
        self.assertEqual(result["POINT_COUNT"],8)
        self.assertEqual(result["UNNAMED_COUNT"],2)
        symbols=self.load(result["SYMBOLS"])
        names=self.load(result["NAMES"])
        self.assertEqual({row["ident"] for row in symbols.getFeatures() if row["ident"].startswith("UNNAMED_")},
                         {"UNNAMED_001","UNNAMED_002"})
        self.assertEqual(names.featureCount(),8)

    def test_no_dashed_and_no_placed_and_tighter_tolerance(self):
        result=self.run_tool(DASHED=False,USE_PLACED=False,TOLERANCE=1)
        self.assertEqual(result["LINE_COUNT"],2)
        self.assertEqual(result["VERTEX_COUNT"],3)
        symbols=self.load(result["SYMBOLS"])
        self.assertEqual({r["ident"] for r in symbols.getFeatures()},{"START","MID","END"})
        self.assertEqual(next(r["symbol_type"] for r in symbols.getFeatures() if r["ident"]=="MID"),"vor")

    def test_empty_or_existing_output_and_failure_cleanup(self):
        output=Path(self.params["OUTPUT_FOLDER"])
        output.mkdir()
        marker=output/"existing.txt"
        marker.write_text("keep")
        with self.assertRaisesRegex(QgsProcessingException,"new or empty"):
            self.run_tool()
        self.assertEqual(marker.read_text(),"keep")
        marker.unlink()
        helper=TOOL.package_tools()
        original=helper.configure_style
        def fail_names(layer,text_layer,symbols,assets):
            if text_layer:
                raise QgsProcessingException("Injected names failure")
            return original(layer,text_layer,symbols,assets)
        helper.configure_style=fail_names
        try:
            with self.assertRaisesRegex(QgsProcessingException,"Injected"):
                self.run_tool()
        finally:
            helper.configure_style=original
        self.assertEqual(list(output.iterdir()),[])
        self.assertEqual(list(self.root.glob(".trajectory-points-*")),[])

    def test_cancellation_and_geographic_crs_are_rejected(self):
        with self.assertRaisesRegex(QgsProcessingException,"metres"):
            self.run_tool(WORK_CRS="EPSG:4326")
        self.feedback.cancel()
        with self.assertRaisesRegex(QgsProcessingException,"Canceled"):
            self.run_tool()
        self.assertFalse(Path(self.params["OUTPUT_FOLDER"]).exists())


if __name__=="__main__":
    unittest.main()
