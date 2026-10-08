# SPDX-License-Identifier: Apache-2.0
import unittest
from luma_viewer.processing import adjust_pixels, crop_bounds

class ProcessingTests(unittest.TestCase):
    def test_identity_and_source_preserved(self):
        data=bytearray([20,100,240,71,5,6,7,99])
        self.assertEqual(adjust_pixels(data,1,2,4,4,{}),data)
        output=adjust_pixels(data,1,2,4,4,{'sat':-100})
        self.assertEqual(output[0],output[1]);self.assertEqual(output[1],output[2])
        self.assertEqual(output[3],71);self.assertEqual(output[7],99)
        self.assertEqual(data,bytearray([20,100,240,71,5,6,7,99]))
    def test_extreme_values_clamp_and_keep_padding(self):
        result=adjust_pixels(bytes([255,255,255,42]),1,1,4,3,{'exp':100})
        self.assertEqual(result,bytes([255,255,255,42]))
    def test_reject_short_buffer(self):
        with self.assertRaises(ValueError):adjust_pixels(b'xx',1,1,3,3,{})
    def test_superseded_preview_stops_before_finishing(self):
        calls=[]
        result=adjust_pixels(bytes([20,30,40])*16*64,16,64,48,3,{'exp':20},
                             cancelled=lambda:(calls.append(1),len(calls)>1)[1])
        self.assertIsNone(result)
        self.assertEqual(len(calls),2)
    def test_crop_constrained_to_source(self):
        self.assertEqual(crop_bounds((-20,-20,1000,1000),100,80),(0,0,100,80))
        with self.assertRaises(ValueError):crop_bounds((0,0,float('nan'),10),100,80)

class EditedCoordinateTests(unittest.TestCase):
    def test_cropped_flipped_straightened_rotated_roundtrip(self):
        from luma_viewer.annotations import DocumentViewport, Viewport
        for rotation in (0,90,180,270):
            viewport=DocumentViewport(Viewport(100,80,300,200,rotation),400,300,(120,90,100,80),True,7,(82,-35))
            original=(170,130)
            result=viewport.to_page(viewport.to_view(original))
            self.assertIsNotNone(result)
            self.assertAlmostEqual(result[0],original[0]);self.assertAlmostEqual(result[1],original[1])
