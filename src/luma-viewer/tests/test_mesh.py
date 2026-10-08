# SPDX-License-Identifier: Apache-2.0
import math
from pathlib import Path
import struct
import tempfile
import unittest
from luma_viewer.mesh import load_mesh, project


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def write(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_obj_polygon_negative_indices_and_rotation(self):
        path = self.write('panel.obj', b'v 0 0 0\nv 2 0 0\nv 2 1 0\nv 0 1 0\nf -4/1 -3/2 -2/3 -1/4\n')
        mesh = load_mesh(path)
        self.assertEqual(mesh.dimensions, (2, 1, 0))
        self.assertEqual(len(mesh.triangles), 2)
        first = project(mesh, 0, 0)
        rotated = project(mesh, math.pi / 2, 0)
        self.assertNotEqual(first, rotated)
        self.assertAlmostEqual(rotated[0][1][0][0], 0)
        self.assertEqual(path.read_bytes().splitlines()[-1], b'f -4/1 -3/2 -2/3 -1/4')

    def test_all_rotations_fit_the_initial_preview(self):
        path = self.write('box.obj', b'v -1 -1 -1\nv 1 1 -1\nv 1 -1 1\nf 1 2 3')
        mesh = load_mesh(path)
        for yaw in (0, .5, 1.5, 3):
            for pitch in (-1.5, -.3, 0, 1.5):
                for _, points, _ in project(mesh, yaw, pitch):
                    for point in points:
                        self.assertLessEqual(math.hypot(*point), .5 + 1e-10)

    def test_ascii_and_binary_stl_are_same_geometry(self):
        ascii_path = self.write('triangle.stl', b'solid sample\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid\n')
        binary = b'solid binary header'.ljust(80, b'\0') + struct.pack('<I', 1)
        binary += struct.pack('<12fH', 0,0,1,0,0,0,1,0,0,0,1,0,0)
        binary_path = self.write('binary.stl', binary)
        self.assertEqual(load_mesh(ascii_path), load_mesh(binary_path))

    def test_missing_nonfinite_and_empty_models_are_refused(self):
        for data in (b'v 0 0 0\nf 0 1 2', b'v NaN 0 0', b'v 0 0 0\nf 1 2 3', b'# no surfaces'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                load_mesh(self.write('broken.obj', data))

    def test_extreme_finite_coordinates_are_bounded_or_refused(self):
        path = self.write('large.obj', b'v -1e308 0 0\nv 1e308 1 0\nv 0 0 1\nf 1 2 3')
        with self.assertRaisesRegex(ValueError, 'extent'):
            load_mesh(path)


if __name__ == '__main__':
    unittest.main()
