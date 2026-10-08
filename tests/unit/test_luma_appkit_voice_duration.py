"""Voicemail can retain total duration while progress and seeking continue."""
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk
from luma_appkit import VoiceClip, install_appkit, install_lumaui
class VoiceDuration(unittest.TestCase):
    def test_duration_label_does_not_change_seek_or_playback(self):
        Gtk.init(); install_appkit(); install_lumaui()
        seeks=[]; playback=[]
        clip=VoiceClip(18,time_mode='duration',on_seek=seeks.append,on_toggle=playback.append)
        clip.set_position(7)
        self.assertEqual(clip.time.get_text(),'0:18')
        self.assertEqual(clip.position,7)
        clip._seek(10)
        self.assertEqual(seeks,[10]);self.assertEqual(clip.position,10)
        clip.toggle();self.assertEqual(playback,[True])
        self.assertEqual(clip.time.get_text(),'0:18')
        remaining=VoiceClip(18,position=7)
        self.assertEqual(remaining.time.get_text(),'0:11')
        with self.assertRaises(ValueError):VoiceClip(18,time_mode='bad')
if __name__=='__main__':unittest.main()
