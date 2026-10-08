"""Pairing QR codes (Connect pairing, Power Mode, Messages accounts) draw with libqrencode when python3-qrcode is absent."""
import builtins
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'prairie-core'))
from prairie_apps import qr_code as qr

URI = 'luma-connect://pair?v=1&host=192.168.1.20&port=47810&pin=' + 'a' * 64 + '&token=' + 'b' * 32 + '&name=Desk'


def finder_at(matrix, top, left):
    """The 7x7 finder pattern: a dark ring, a light ring and a dark 3x3 centre."""
    for y in range(7):
        for x in range(7):
            ring = max(abs(y - 3), abs(x - 3))
            if matrix[top + y][left + x] != (ring != 2):
                return False
    return True


class QrTest(unittest.TestCase):
    def check(self, matrix):
        size = len(matrix)
        self.assertTrue(all(len(row) == size for row in matrix))
        q = qr.QUIET_ZONE
        self.assertFalse(any(matrix[0]) or any(row[0] for row in matrix), 'quiet zone is light')
        self.assertTrue(finder_at(matrix, q, q))
        self.assertTrue(finder_at(matrix, q, size - q - 7))
        self.assertTrue(finder_at(matrix, size - q - 7, q))
        self.assertEqual((size - 2 * q - 17) % 4, 0, 'a QR symbol is 17 + 4 x version modules wide')

    def test_libqrencode_without_python_qrcode(self):
        real_import = builtins.__import__

        def no_qrcode(name, *args, **kwargs):
            if name == 'qrcode' or name.startswith('qrcode.'):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        with mock.patch('builtins.__import__', no_qrcode):
            matrix = qr.matrix(URI)
        self.assertIsNotNone(matrix, 'libqrencode is required by the package')
        self.check(matrix)

    def test_no_encoder_means_no_code(self):
        with mock.patch.object(qr, '_python_qrcode', side_effect=ImportError), \
                mock.patch.object(qr, '_libqrencode', side_effect=OSError):
            self.assertIsNone(qr.matrix(URI))


if __name__ == '__main__':
    unittest.main()
