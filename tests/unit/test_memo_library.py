import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from prairie_apps.memo_library import RecordingLibrary, reduce_peaks, sidecar, validated_cues


class MemoLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = RecordingLibrary(self.root/'Recordings',self.root/'Legacy')
        self.audio = self.library.root/'Recording.ogg'
        self.audio.write_bytes(b'fixture audio bytes')

    def test_transcript_only_search_and_unknown_duration(self):
        self.assertIsNone(self.library.recordings()[0].duration)
        self.library.update(self.audio, transcript=[{'start':0,'end':5,'text':'Bring the telescope'}],
                            transcript_state='ready')
        record = self.library.recordings()[0]
        self.assertEqual(record.match('TELESCOPE'),'Bring the telescope')
        self.assertIsNone(record.match('unrelated'))
        self.assertEqual(record.match('recording'),'')

    def test_delete_and_restore_preserve_audio_and_sidecars(self):
        self.library.update(self.audio,title='Important recording')
        peaks = self.audio.with_name(self.audio.name+'.peaks.json')
        peaks.write_text('[0, 1, 0]')
        originals = {p.name:p.read_bytes() for p in (self.audio,sidecar(self.audio),peaks)}
        deleted = self.library.trash(self.audio)
        self.assertEqual(self.library.recordings(),())
        self.assertEqual(self.library.restore(deleted),self.audio)
        self.assertEqual({name:(self.audio.parent/name).read_bytes() for name in originals}, originals)

    def test_restore_never_overwrites_and_deletion_rolls_back_on_failure(self):
        self.library.update(self.audio,title='Keep me')
        original_rename = Path.rename
        def fail_metadata(path, target):
            if path.name.endswith('.memo.json'):
                raise OSError('injected disk error')
            return original_rename(path,target)
        with patch.object(Path,'rename',fail_metadata):
            with self.assertRaises(OSError):
                self.library.trash(self.audio)
        self.assertEqual(self.audio.read_bytes(),b'fixture audio bytes')
        identifier = self.library.trash(self.audio)
        self.audio.write_bytes(b'new file')
        with self.assertRaises(FileExistsError):
            self.library.restore(identifier)
        self.assertEqual(self.audio.read_bytes(),b'new file')

    def test_rejects_external_symlink_and_malformed_cues(self):
        external = self.root/'outside.ogg';external.write_bytes(b'external')
        link = self.library.root/'link.ogg';link.symlink_to(external)
        with self.assertRaises(ValueError):self.library.trash(link)
        with self.assertRaises(ValueError):self.library.trash(external)
        self.assertEqual(validated_cues([{'start':float('nan'),'end':2,'text':'bad'},
            {'start':2,'end':1,'text':'bad'}, {'start':1,'end':2,'text':'valid'}]),
            ({'start':1.0,'end':2.0,'text':'valid'},))

    def test_waveform_reduction_is_stable_and_preserves_transients(self):
        self.assertEqual(reduce_peaks([0,.1,1,.2,0,.3],3),(.1,1,.3))
        self.assertEqual(reduce_peaks([.5],4),(.5,.5,.5,.5))
        self.assertEqual(reduce_peaks([],4),())


if __name__ == '__main__':unittest.main()
