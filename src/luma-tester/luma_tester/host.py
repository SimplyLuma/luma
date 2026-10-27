# SPDX-License-Identifier: Apache-2.0
"""Host services used by the shared VM controller."""
import csv
import ctypes
import os
from pathlib import Path
import platform
import socket
import subprocess


def windows():
    return platform.system() == 'Windows'


def lock(stream):
    if windows():
        import msvcrt
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'\0');stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def private_directory(path):
    path = Path(path)
    created = not path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if windows():
        if created:
            identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
            sid = next(csv.reader(identity.strip().splitlines()))[1]
            if not sid.startswith('S-1-'):
                raise RuntimeError('Could not identify the Windows session owner.')
            subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                '*'+sid+':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    elif path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise RuntimeError('Runtime directory must be private and owned by this user.')
    return path


def whpx_available():
    if not windows():
        return False
    try:
        library = ctypes.WinDLL('WinHvPlatform.dll')
        present = ctypes.c_int()
        written = ctypes.c_uint()
        result = library.WHvGetCapability(0, ctypes.byref(present),
            ctypes.sizeof(present), ctypes.byref(written))
        return result == 0 and bool(present.value)
    except (OSError, AttributeError):
        return False


def free_port():
    with socket.socket() as stream:
        stream.bind(('127.0.0.1', 0))
        return stream.getsockname()[1]


def display_address(path):
    from gi.repository import Gio
    if windows():
        import json
        endpoint = json.loads(Path(path).read_text())
        return Gio.InetSocketAddress.new_from_string('127.0.0.1', endpoint['port'])
    return Gio.UnixSocketAddress.new(str(path))


def display_auth(connection, types, path, namespace):
    values = [types.get_nth(i) for i in range(types.n_values)]
    values = [v.get_uint() if hasattr(v, 'get_uint') else v for v in values]
    if windows():
        if namespace.ConnectionAuth.VNC not in values:
            return False
        connection.set_auth_type(namespace.ConnectionAuth.VNC)
    elif namespace.ConnectionAuth.NONE in values:
        connection.set_auth_type(namespace.ConnectionAuth.NONE)
    else:
        return False
    return True


def display_credentials(connection, credentials, path, namespace):
    import json
    endpoint = json.loads(Path(path).read_text())
    connection.set_credential(namespace.ConnectionCredential.PASSWORD, endpoint['password'])
