# SPDX-License-Identifier: GPL-3.0-only
"""Real SCM_RIGHTS and GDK texture/lease regressions; no display or browser mock."""
import array
from collections import deque
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from frame_receiver import _FrameConnection, Gdk
from test_shared_frame import frame


class ReceiverShmTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='viola-shm-socket-')
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'pixels'
        red = b'\x00\x00\xff\xff'
        self.body = b'HEAD' + (red*2 + b'PAD!')*2
        self.path.write_bytes(self.body)
        self.fd = os.open(self.path, os.O_RDONLY)
        self.addCleanup(os.close, self.fd)
        peer, self.sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(self.sender.close)
        self.queue, self.frames, self.errors = deque(), [], []
        self.addCleanup(patch.stopall)
        patch('frame_receiver.GLib.idle_add', side_effect=lambda f,*a: self.queue.append((f,a))).start()
        owner = SimpleNamespace(on_clipboard=None, on_cursor=None,
            on_frame=lambda t,m: self.frames.append((t,m)), on_error=self.errors.append,
            on_window=None, on_window_closed=None)
        self.connection = _FrameConnection(owner, peer)
        self.connection.start(os.getpid())
        self.addCleanup(self.close)
        self.send(dict(kind='window',version=1,window_id=7,
            sidebar_target_id='sidebar',overlay_target_id='overlay'))
        self.wait(lambda: self.connection.window_id == 7)

    def close(self):
        self.connection.stopped.set()
        self.sender.shutdown(socket.SHUT_RDWR)
        self.connection.worker.join(2)
        while self.queue:
            fn,args = self.queue.popleft(); fn(*args)
        self.assertFalse(self.connection.worker.is_alive())
        self.assertFalse(self.connection.pending)

    def wait(self, condition):
        end = time.monotonic()+2
        while not condition():
            if time.monotonic() >= end: self.fail('Bounded receiver condition timed out')
            time.sleep(.005)

    def send(self, metadata, descriptors=()):
        rights = [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',descriptors))] if descriptors else []
        self.sender.sendmsg([json.dumps(metadata).encode()],rights)

    def consume(self):
        self.wait(lambda: bool(self.queue))
        fn,args = self.queue.popleft(); fn(*args)

    def test_real_readonly_frame_creates_texture_before_credit_and_retains_pixels(self):
        self.send(frame(),[self.fd])
        self.wait(lambda: len(self.queue)==1)
        self.assertEqual(self.connection.pending,{1})
        self.sender.settimeout(.02)
        with self.assertRaises(socket.timeout): self.sender.recv(4)
        self.consume()
        self.assertEqual(self.sender.recv(4),struct.pack('!I',1))
        self.assertEqual(self.connection.imported,1)
        self.assertEqual(self.connection.released,1)
        self.assertFalse(self.connection.pending)
        self.assertFalse(self.errors)
        texture,_ = self.frames[0]
        self.assertEqual((texture.get_width(),texture.get_height()),(2,2))
        self.path.write_bytes(b'X'*len(self.body))
        downloader = Gdk.TextureDownloader.new(texture)
        downloader.set_format(Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED)
        pixels,stride = downloader.download_bytes()
        self.assertEqual(bytes(pixels.get_data())[:8],b'\xff\0\0\xff'*2)
        self.assertEqual(bytes(pixels.get_data())[stride:stride+8],b'\xff\0\0\xff'*2)

    def test_p3_color_state_is_preserved(self):
        metadata = frame(); metadata['color_primaries']=12
        self.send(metadata,[self.fd]); self.consume()
        color = self.frames[0][0].get_color_state().create_cicp_params()
        self.assertEqual(color.get_color_primaries(),12)
        self.assertEqual(color.get_transfer_function(),13)

    def test_close_before_gtk_import_releases_descriptor_and_exact_credit(self):
        self.send(frame(),[self.fd]); self.wait(lambda: bool(self.queue))
        self.connection.stopped.set(); self.consume()
        self.sender.settimeout(1)
        self.assertEqual(self.sender.recv(4),struct.pack('!I',1))
        self.assertEqual(self.connection.released,1)
        self.assertEqual(self.connection.imported,0)
        self.assertFalse(self.frames)

    def test_texture_creation_failure_releases_exact_credit_once(self):
        self.send(frame(),[self.fd]); self.wait(lambda: bool(self.queue))
        with patch('frame_receiver.Gdk.MemoryTextureBuilder.new',side_effect=RuntimeError('actual import failure')):
            self.consume()
        self.sender.settimeout(1)
        self.assertEqual(self.sender.recv(4),struct.pack('!I',1))
        self.assertEqual(self.connection.released,1)
        self.assertEqual(self.errors,['actual import failure'])
        self.assertFalse(self.frames)

    def test_wrong_window_and_extra_descriptors_are_rejected_without_credit(self):
        metadata=frame();metadata['window_id']=99
        self.send(metadata,[self.fd,self.fd])
        self.wait(lambda:self.connection.disconnected)
        self.consume()
        self.assertIn('different window',self.errors[0])
        self.assertFalse(self.connection.pending)
        self.assertEqual(self.connection.released,0)

    def test_peer_must_be_the_owned_engine_process(self):
        peer,sender=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        other=_FrameConnection(self.connection.owner,peer)
        try:
            other.start(os.getpid()+1)
            self.wait(lambda:other.disconnected);self.consume()
            self.assertIn('not the owned Chromium child',self.errors[0])
            self.assertEqual(other.released,0)
        finally:
            sender.close();other.close()

    def test_duplicate_frame_does_not_credit_an_unaccepted_id(self):
        self.send(frame(),[self.fd]);self.wait(lambda:bool(self.queue))
        self.send(frame(),[self.fd]);self.wait(lambda:self.connection.disconnected)
        self.assertEqual(self.connection.pending,{1})
        while self.queue:self.consume()
        self.assertEqual(self.connection.released,1)
        self.assertTrue(any('Duplicate' in value for value in self.errors))

    def test_extra_descriptors_are_rejected_for_the_correct_window(self):
        self.send(frame(),[self.fd,self.fd])
        self.wait(lambda:self.connection.disconnected); self.consume()
        self.assertIn('descriptor count',self.errors[0])
        self.assertEqual(self.connection.released,0)
        self.assertFalse(self.frames)

    def test_consumer_failure_does_not_duplicate_released_credit(self):
        self.connection.on_frame=lambda *_: (_ for _ in ()).throw(RuntimeError('consumer failed'))
        self.send(frame(),[self.fd]); self.consume()
        self.sender.settimeout(1)
        self.assertEqual(self.sender.recv(4),struct.pack('!I',1))
        self.assertEqual(self.connection.released,1)
        self.assertEqual(self.errors,['consumer failed'])
        self.assertFalse(self.connection.pending)

    def test_dma_credit_still_follows_texture_finalizer(self):
        class Builder:
            def __getattr__(self,name):
                if name.startswith('set_'):return lambda *_:None
                raise AttributeError(name)
            def build(self,callback,identity):
                self.release=lambda:callback(identity)
                return self
        builder=Builder()
        metadata=dict(version=1,id=1,window_id=7,target_id='owned-page',
            width=2,height=2,fourcc='AR24',modifier='0',
            planes=[dict(stride=8,offset='0',size='28')])
        self.send(metadata,[self.fd]); self.wait(lambda:bool(self.queue))
        with patch('frame_receiver.Gdk.DmabufTextureBuilder.new',return_value=builder):self.consume()
        self.assertEqual(self.connection.pending,{1})
        self.assertEqual(self.connection.released,0)
        builder.release();builder.release()
        self.sender.settimeout(1)
        self.assertEqual(self.sender.recv(4),struct.pack('!I',1))
        self.assertEqual(self.connection.released,1)
        self.assertFalse(self.connection.pending)

    def test_fourth_unconsumed_frame_is_rejected_and_prior_leases_remain_bounded(self):
        for identity in range(1,5):
            metadata=frame();metadata['id']=identity
            self.send(metadata,[self.fd])
        self.wait(lambda:self.connection.disconnected)
        self.assertEqual(self.connection.pending,{1,2,3})
        while self.queue:
            self.consume()
        self.assertEqual(self.connection.released,3)
        self.assertFalse(self.connection.pending)
        self.assertTrue(any('excess' in error for error in self.errors))

if __name__=='__main__':unittest.main()
