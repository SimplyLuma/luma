# SPDX-License-Identifier: Apache-2.0
"""Host services used by the shared VM controller."""
import csv
import ctypes
import os
from pathlib import Path
import platform
import socket
import subprocess


def run_ssh(argv, **kwargs):
    """Run a noninteractive SSH command without Win32 anonymous output pipes.

    The bundled Windows OpenSSH can stall before writing even its debug banner
    with Python PIPE handles. Private temporary files also support binary stdin
    uploads, preserve exit status, and let subprocess enforce its normal timeout.
    """
    if not windows():
        return subprocess.run(argv, **kwargs)
    import contextlib
    import locale
    import shutil
    import sys
    import tempfile
    options = dict(kwargs)
    capture = options.pop('capture_output', False)
    if capture:
        if options.get('stdout') is not None or options.get('stderr') is not None:
            raise ValueError('stdout and stderr cannot be combined with capture_output')
        options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    check = options.pop('check', False)
    text = options.pop('text', False)
    text = options.pop('universal_newlines', False) or text
    encoding = options.pop('encoding', None)
    errors = options.pop('errors', None)
    text = text or encoding is not None or errors is not None
    encoding = encoding or ('utf-8' if sys.flags.utf8_mode else locale.getencoding())
    data = options.pop('input', None)
    if data is not None and Path(str(argv[0])).name.lower() in ('ssh', 'ssh.exe'):
        payload = data.encode(encoding, errors or 'strict') if text else data
        return _ssh_upload_input(argv, payload, dict(options, check=check, text=text, encoding=encoding if text else None, errors=errors if text else None))
    with contextlib.ExitStack() as stack:
        outputs = {}
        for name in ('stdout', 'stderr'):
            target = options.get(name)
            if target is None or target == subprocess.PIPE:
                stream = stack.enter_context(tempfile.TemporaryFile())
                outputs[name] = (stream, target)
                options[name] = stream
        if data is not None:
            if 'stdin' in options:
                raise ValueError('stdin and input cannot be combined')
            stream = stack.enter_context(tempfile.TemporaryFile())
            stream.write(data.encode(encoding, errors or 'strict') if text else data)
            stream.seek(0)
            options['stdin'] = stream
        else:
            options.setdefault('stdin', subprocess.DEVNULL)

        def collect(name):
            if name not in outputs:
                return None
            stream, target = outputs[name]
            stream.seek(0)
            if target == subprocess.PIPE:
                body = stream.read()
                return body.decode(encoding, errors or 'strict').replace('\r\n', '\n').replace('\r', '\n') if text else body
            destination = getattr(sys, name)
            if destination is not None:
                if hasattr(destination, 'buffer'):
                    shutil.copyfileobj(stream, destination.buffer)
                    destination.buffer.flush()
                else:
                    destination.write(stream.read().decode(encoding, errors or 'replace'))
                    destination.flush()
            return None

        try:
            result = subprocess.run(argv, check=False, **options)
        except subprocess.TimeoutExpired as exc:
            exc.output, exc.stderr = collect('stdout'), collect('stderr')
            raise
        result.stdout, result.stderr = collect('stdout'), collect('stderr')
        if check:
            result.check_returncode()
        return result


def _ssh_upload_input(argv, payload, options):
    """Use batch SFTP for Windows stdin: OpenSSH's inherited input also stalls."""
    import re
    import shutil
    import tempfile
    import time
    deadline = time.monotonic() + options['timeout'] if options.get('timeout') is not None else None

    def remaining():
        if deadline is None:
            return None
        value = deadline - time.monotonic()
        if value <= 0:
            raise subprocess.TimeoutExpired(argv, options['timeout'])
        return value

    target = None
    try:
        created = run_ssh(list(argv[:-1]) + ['umask 077; mktemp /tmp/try-luma-input.XXXXXXXXXX'],
                          capture_output=True, text=True, check=True, timeout=remaining())
        target = created.stdout.strip()
        if not re.fullmatch(r'/tmp/try-luma-input\.[A-Za-z0-9]{10}', target):
            target = None
            raise RuntimeError('Guest did not return a private input file.')
        with tempfile.TemporaryDirectory(prefix='try-luma-input-') as directory:
            local = Path(directory) / 'input'
            local.write_bytes(payload)
            batch = Path(directory) / 'upload.sftp'
            batch.write_text('put "' + local.as_posix().replace('"', '\\"') + '" "' + target + '"\n', encoding='utf-8')
            ssh = shutil.which(str(argv[0]))
            if not ssh:
                raise FileNotFoundError('Windows OpenSSH is unavailable.')
            sftp = Path(ssh).with_name('sftp.exe')
            if not sftp.is_file():
                raise FileNotFoundError('Windows SFTP is unavailable.')
            flags = ['-P' if flag == '-p' else flag for flag in argv[1:-2]]
            run_ssh([str(sftp), '-b', str(batch), *flags, argv[-2]],
                    capture_output=True, check=True, timeout=remaining())
            options['timeout'] = remaining()
            return run_ssh(list(argv[:-1]) + ['(' + argv[-1] + ') < ' + target], **options)
    finally:
        if target is not None:
            try:
                run_ssh(list(argv[:-1]) + ['rm -f -- ' + target],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass


def ssh_output(argv, **kwargs):
    return run_ssh(argv, stdout=subprocess.PIPE, check=True, **kwargs).stdout


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


def local_control_socket(path, timeout=5):
    """Connect to a private QMP socket, including Windows' native AF_UNIX."""
    if not windows():
        stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            stream.settimeout(timeout)
            stream.connect(str(path))
            return stream
        except BaseException:
            stream.close()
            raise
    # Windows 10+ and bundled QEMU support AF_UNIX. CPython's Windows socket
    # module does not expose its address structure, so connect through Winsock.
    # Keep QMP in the owner-only runtime directory, without a listening TCP port.
    encoded = str(path).encode('utf-8')
    if not encoded or len(encoded) >= 108 or b'\0' in encoded:
        raise ValueError('The local control socket path is too long or invalid.')
    class Address(ctypes.Structure):
        _fields_ = [('family', ctypes.c_ushort), ('path', ctypes.c_char * 108)]
    address = Address(1, encoded)
    library = ctypes.WinDLL('ws2_32')
    connect = library.connect
    connect.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    connect.restype = ctypes.c_int
    stream = socket.socket(1, socket.SOCK_STREAM)
    try:
        if connect(stream.fileno(), ctypes.byref(address), ctypes.sizeof(address)) != 0:
            code = library.WSAGetLastError()
            raise OSError(code, 'Could not connect to the local VM controls.')
        stream.settimeout(timeout)
        return stream
    except BaseException:
        stream.close()
        raise
