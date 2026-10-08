import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from luma_relay.presentation import bounded_size, read_bounded, registry_values, installed_components
class Facts(unittest.TestCase):
    def test_size_never_counts_external_link_and_refuses_partial(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); (root/'owned').write_bytes(b'123'); (root/'outside').symlink_to('/etc/passwd')
            self.assertEqual(bounded_size(root),3)
            self.assertIsNone(bounded_size(root, limit=0))
            self.assertEqual(read_bounded(root/'outside'),'')
    def test_registry_reads_actual_section_and_types(self):
        raw='[Software\\\\Wine] 123\n"Version"="win10"\n[Control Panel\\\\Desktop]\n"LogPixels"=dword:00000078\n'
        self.assertEqual(registry_values(raw,r'Software\Wine'), {'Version':'win10'})
        self.assertEqual(registry_values(raw,r'Control Panel\Desktop'), {'LogPixels':'120'})
    def test_component_state_comes_from_winetricks_log(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);(root/'winetricks.log').write_text('dotnet48\nvcrun2022\nnot a completed verb\n')
            self.assertEqual(installed_components(root),{'dotnet48','vcrun2022'})
if __name__=='__main__':unittest.main()
