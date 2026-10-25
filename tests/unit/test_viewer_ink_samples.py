# SPDX-License-Identifier: Apache-2.0
import ast
from dataclasses import replace
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

root=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('viewer_annotations',root/'src/luma-viewer/luma_viewer/annotations.py')
import sys
annotations=importlib.util.module_from_spec(spec);sys.modules[spec.name]=annotations;spec.loader.exec_module(annotations)
def function(file,name,scope):
 tree=ast.parse((root/'src/luma-viewer/luma_viewer'/file).read_text())
 node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
 exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),file,'exec'),scope)
 return scope[name]
update=function('application.py','_mark_update',{'replace':replace,'Gdk':NS(ModifierType=NS(SHIFT_MASK=1))})
path=function('drawing.py','ink_path',{})
class InkSamplesTests(unittest.TestCase):
 def window(self,tool='ink'):
  return NS(pan_origin=None,compare=False,drag_origin=(10,20),drag_viewport=NS(to_page=lambda p,**kw:(p[0]/2,p[1]/2)),tool=tool,selected=None,pending=annotations.Mark(tool,'#000000',(5,10),(5,10),points=((5,10),)),marks_area=Mock())
 def test_coalesced_pen_samples_are_mapped_in_order_in_one_redraw(self):
  w=self.window();samples=((10,20),(12,24),(14,26),(14,26),(16,24))
  update(w,None,10,0,samples=samples)
  self.assertEqual(w.pending.points,((5,10),(6,12),(7,13),(8,12),(10,10)))
  self.assertEqual(w.pending.end,(10,10));w.marks_area.queue_draw.assert_called_once()
 def test_shape_uses_current_endpoint_without_freehand_history(self):
  w=self.window('box');gesture=NS(get_current_event_state=lambda:0)
  update(w,gesture,10,20,samples=((12,22),))
  self.assertEqual(w.pending.end,(10,20));self.assertEqual(w.pending.points,((5,10),))
 def test_smooth_path_retains_endpoints_and_stays_inside_sample_bounds(self):
  cr=Mock();points=((0,0),(10,20),(30,10),(40,30))
  path(cr,points)
  cr.move_to.assert_called_once_with(0,0);cr.line_to.assert_called_once_with(40,30)
  self.assertEqual(cr.curve_to.call_count,2)
  for call in cr.curve_to.call_args_list:
   for x,y in zip(call.args[::2],call.args[1::2]):self.assertTrue(0<=x<=40 and 0<=y<=30)
 def test_single_point_and_straight_stroke_do_not_invent_curves(self):
  for points in (((1,2),),((1,2),(3,4))):
   cr=Mock();path(cr,points);cr.curve_to.assert_not_called()
