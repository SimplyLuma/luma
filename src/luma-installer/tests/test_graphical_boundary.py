import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from luma_installer.launcher import _BusBoundary, _launch_appimage, _launch_deb


def filtered(runtime):
    # What _start_bus_proxy hands back: the application's filtered view of
    # the accessibility bus, never the bus itself.
    socket=runtime/'luma-installer-bus/fixture.a11y'
    socket.parent.mkdir(parents=True,exist_ok=True)
    socket.touch()
    return _BusBoundary(MagicMock(),[],accessibility=socket),None

class GraphicalBoundary(unittest.TestCase):
    def test_confined_domain_and_private_home(self):
        with tempfile.TemporaryDirectory() as d, patch('pathlib.Path.home',return_value=Path(d)), patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(Path(d)/'run')},clear=True), patch('luma_installer.launcher._start_bus_proxy',return_value=filtered(Path(d)/'run')), patch('luma_installer.launcher.launch_guard.run',return_value=(0,1.0,[])) as run, patch('luma_installer.launcher._person_folders',return_value=[Path(d).resolve()/'Documents']):
            runtime=Path(d)/'run'
            (runtime/'at-spi').mkdir(parents=True)
            (runtime/'at-spi/bus').touch()
            for name in ('pipewire-0','pipewire-0-manager','pipewire-0-application'):
                (runtime/name).touch()
            self.assertEqual(_launch_deb({'sha256':'fixture','image':'localhost/fixture','command':'fixture'}),0)
            args=run.call_args.args[0]
            # X: no cookie, and nothing of the session's X sockets.
            self.assertFalse(any('XAUTHORITY' in a or a.startswith('/tmp/.X11-unix:') or a.startswith('DISPLAY=:0') for a in args))
            # Sound: the socket the session keeps for applications, never its own.
            self.assertIn(f'{runtime}/pipewire-0-application:/run/user/{os.getuid()}/pipewire-0:rw',args)
            self.assertFalse(any(a.startswith((f'{runtime}/pipewire-0:',f'{runtime}/pipewire-0-manager:')) for a in args))
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
            # It is the filtered bus: its own tree, nobody else's.
            self.assertIn(f'{runtime}/luma-installer-bus/fixture.a11y:/run/user/{os.getuid()}/at-spi/bus:rw',args)
            self.assertIn(f'AT_SPI_BUS_ADDRESS=unix:path=/run/user/{os.getuid()}/at-spi/bus',args)
            self.assertFalse(any(a.startswith(f'{runtime}/at-spi/bus:') for a in args))

    def test_sandboxed_application_gets_the_accessibility_bus_and_desktop_settings(self):
        with tempfile.TemporaryDirectory() as d:
            home,runtime=Path(d)/'home',Path(d)/'run'
            for relative in ('at-spi','pulse'):
                (runtime/relative).mkdir(parents=True)
            (runtime/'at-spi/bus').touch()
            (runtime/'pipewire-0').touch()
            capsule_runtime=Path(f'/run/user/{os.getuid()}')
            with patch('pathlib.Path.home',return_value=home), \
                 patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(runtime)},clear=True), \
                 patch('luma_installer.launcher._start_bus_proxy',return_value=filtered(runtime)), \
                 patch('luma_installer.launcher.launch_guard.run',return_value=(0,1.0,[])) as run, \
                 patch('luma_installer.launcher._person_folders',return_value=[]):
                home.mkdir()
                self.assertEqual(_launch_appimage({'sha256':'fixture','format':'appimage',
                                                   'root':str(Path(d)/'AppDir'),'command':'/app/AppRun'}),0)
                args=run.call_args.args[0]
                # The bus lives at the address a toolkit reads from the session
                # bus, and its directory exists before the bind.
                self.assertIn(str(capsule_runtime/'at-spi'),args)
                # A session without the application socket shares no PipeWire socket.
                self.assertNotIn(str(runtime/'pipewire-0'),args)
                proxied=str(runtime/'luma-installer-bus/fixture.a11y')
                self.assertEqual(args[args.index(proxied)-1],'--ro-bind')
                self.assertEqual(args[args.index(proxied)+1],str(capsule_runtime/'at-spi/bus'))
                self.assertNotIn(str(runtime/'at-spi/bus'),args)
                self.assertEqual(run.call_args.kwargs['env']['AT_SPI_BUS_ADDRESS'],
                                 f"unix:path={capsule_runtime/'at-spi/bus'}")
                # An application inside the sandbox reads the desktop's font,
                # text scale and theme through the portal, as a packaged one does.
                self.assertEqual(run.call_args.kwargs['env']['GTK_USE_PORTAL'],'1')

if __name__=='__main__': unittest.main()
