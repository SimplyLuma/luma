import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from luma_installer.launcher import _launch_appimage, _launch_deb

class GraphicalBoundary(unittest.TestCase):
    def test_confined_domain_and_private_home(self):
        with tempfile.TemporaryDirectory() as d, patch('pathlib.Path.home',return_value=Path(d)), patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(Path(d)/'run')},clear=True), patch('luma_installer.launcher._start_bus_proxy',return_value=(None,None)), patch('luma_installer.launcher.launch_guard.run',return_value=(0,1.0,[])) as run, patch('luma_installer.launcher._person_folders',return_value=[Path(d).resolve()/'Documents']):
            runtime=Path(d)/'run'
            (runtime/'at-spi').mkdir(parents=True)
            (runtime/'at-spi/bus').touch()
            self.assertEqual(_launch_deb({'sha256':'fixture','image':'localhost/fixture','command':'fixture'}),0)
            args=run.call_args.args[0]
            self.assertIn('label=type:luma_application.process',args)
            self.assertTrue(any(a.startswith('label=level:s0:c') for a in args))
            self.assertIn('unmask=/proc/*',args)
            self.assertIn('--userns=keep-id',args)
            self.assertNotIn('--privileged',args)
            self.assertNotIn('label=disable',args)
            # Desktop OAuth callbacks must share the browser's loopback namespace.
            self.assertIn('--network=host',args)
            home=Path(d).resolve()
            # The private home sits at the real home path so file-chooser paths
            # resolve inside; only the person's folders are shared, never home.
            self.assertIn(f'{home}/.local/share/luma/installer/data/fixture:{home}:rw,Z',args)
            self.assertTrue(any('/installer/data/fixture:/home/luma:rw' in a for a in args))
            self.assertIn(f'HOME={home}',args)
            self.assertIn(f'{home}/Documents:{home}/Documents:rw',args)
            self.assertFalse(any(a.startswith(f'{home}:') for a in args))
            self.assertFalse(any('/run/dbus/system_bus_socket' in a for a in args))
            # The accessibility bus is in the capsule: an application must not
            # start by reporting that this desktop has no accessibility.
            self.assertIn(f'{runtime}/at-spi/bus:/run/user/{os.getuid()}/at-spi/bus:rw',args)

    def test_sandboxed_application_gets_the_accessibility_bus_and_desktop_settings(self):
        with tempfile.TemporaryDirectory() as d:
            home,runtime=Path(d)/'home',Path(d)/'run'
            for relative in ('at-spi','pulse'):
                (runtime/relative).mkdir(parents=True)
            (runtime/'at-spi/bus').touch()
            capsule_runtime=Path(f'/run/user/{os.getuid()}')
            with patch('pathlib.Path.home',return_value=home), \
                 patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(runtime)},clear=True), \
                 patch('luma_installer.launcher._start_bus_proxy',return_value=(None,None)), \
                 patch('luma_installer.launcher.launch_guard.run',return_value=(0,1.0,[])) as run, \
                 patch('luma_installer.launcher._person_folders',return_value=[]):
                home.mkdir()
                self.assertEqual(_launch_appimage({'sha256':'fixture','format':'appimage',
                                                   'root':str(Path(d)/'AppDir'),'command':'/app/AppRun'}),0)
                args=run.call_args.args[0]
                # The bus lives at the address a toolkit reads from the session
                # bus, and its directory exists before the bind.
                self.assertIn(str(capsule_runtime/'at-spi'),args)
                self.assertEqual(args[args.index(str(runtime/'at-spi/bus'))-1],'--ro-bind')
                self.assertEqual(args[args.index(str(runtime/'at-spi/bus'))+1],
                                 str(capsule_runtime/'at-spi/bus'))
                # An application inside the sandbox reads the desktop's font,
                # text scale and theme through the portal, as a packaged one does.
                self.assertEqual(run.call_args.kwargs['env']['GTK_USE_PORTAL'],'1')

if __name__=='__main__': unittest.main()
