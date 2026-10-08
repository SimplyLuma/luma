"""Library grids can retain a measured column count through narrow resizes."""
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk
from luma_appkit import MediaGrid,MediaItem,install_appkit,install_lumaui
class FixedGrid(unittest.TestCase):
 def test_three_columns_resize_without_clipping(self):
  Gtk.init();install_appkit();install_lumaui()
  grid=MediaGrid([MediaItem(str(n),title=str(n)) for n in range(9)],kind='library',scrolls=False,columns=3,gap=4)
  for width in (328,370,468):
   height=grid.measure(Gtk.Orientation.VERTICAL,width)[1]
   grid.allocate(width,height,-1,None)
   self.assertEqual(grid._columns,3)
   for tile in grid._flat:
    ok,b=tile.compute_bounds(grid);self.assertTrue(ok)
    self.assertGreaterEqual(b.get_x(),0)
    self.assertLessEqual(b.get_x()+b.get_width(),width)
    self.assertLessEqual(b.get_y()+b.get_height(),height)
  with self.assertRaises(ValueError):MediaGrid(columns=0)
  with self.assertRaises(ValueError):MediaGrid(gap=-1)
if __name__=='__main__':unittest.main()
