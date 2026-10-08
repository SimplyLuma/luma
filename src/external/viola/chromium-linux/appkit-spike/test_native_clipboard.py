# SPDX-License-Identifier: GPL-3.0-only
import fcntl
import os
import unittest
from native_clipboard import decode_offer, MAX_BYTES


@unittest.skipUnless(hasattr(os, 'memfd_create'), 'Linux sealed clipboard transport')
class ClipboardTest(unittest.TestCase):
    def offer(self, payload, seal=True):
        fd = os.memfd_create('clipboard-test', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        self.addCleanup(os.close, fd)
        os.write(fd, payload)
        if seal:
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        return fd

    def test_preserves_unicode_and_html_copy_payload(self):
        text, html = 'Viola café 🌒'.encode(), b'<b>Viola</b>'
        metadata = dict(version=1,size=len(text)+len(html),formats=[
            dict(mime='text/plain',offset=0,size=len(text)),
            dict(mime='text/html',offset=len(text),size=len(html))])
        self.assertEqual(decode_offer(metadata,[self.offer(text+html)]),
                         {'text/plain':text,'text/html':html})

    def test_rejects_unsealed_mismatched_and_overlapping_payloads(self):
        metadata = dict(version=1,size=3,formats=[dict(mime='text/plain',offset=0,size=3)])
        with self.assertRaises(ValueError):
            decode_offer(metadata,[self.offer(b'abc',False)])
        for changes in (dict(size=MAX_BYTES+1),dict(size=4),
                        dict(formats=[dict(mime='text/plain',offset=1,size=2)]),
                        dict(formats=[dict(mime='text/plain',offset=0,size=2)]),
                        dict(formats=[dict(mime='bad',offset=0,size=3)])):
            with self.assertRaises(ValueError):
                decode_offer({**metadata,**changes},[self.offer(b'abc')])
