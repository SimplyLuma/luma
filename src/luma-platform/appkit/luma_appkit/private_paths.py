# SPDX-License-Identifier: Apache-2.0
"""Private per-user application directories on POSIX and Windows."""
import os
from pathlib import Path
import stat


def ensure_private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise PermissionError('The application directory is unsafe.')
    if os.name != 'nt':
        if info.st_uid != os.getuid():raise PermissionError('The application directory belongs to another user.')
        path.chmod(0o700)
        return
    _restrict_windows_directory(path)


def _restrict_windows_directory(path):
    import ctypes
    from ctypes import wintypes as w
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    api.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    api.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, pointer, w.DWORD, ctypes.POINTER(w.DWORD)]
    api.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD, ctypes.POINTER(pointer), pointer, pointer, pointer, ctypes.POINTER(pointer)]
    api.GetNamedSecurityInfoW.restype = w.DWORD
    api.EqualSid.argtypes = [pointer, pointer]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(pointer), pointer]
    api.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, pointer]
    token = w.HANDLE()
    descriptor = pointer()
    owner = pointer()
    restricted = pointer()
    if not api.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise PermissionError('The application directory owner could not be verified.')
    try:
        length = w.DWORD()
        api.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        if not 0 < length.value <= 65536:raise PermissionError('Invalid directory ownership information.')
        user = ctypes.create_string_buffer(length.value)
        if not api.GetTokenInformation(token, 1, user, length, ctypes.byref(length)):
            raise PermissionError('The application directory owner could not be verified.')
        current_sid = ctypes.cast(user, ctypes.POINTER(pointer))[0]
        if api.GetNamedSecurityInfoW(str(path), 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(descriptor)) != 0 or not api.EqualSid(owner, current_sid):
            raise PermissionError('The application directory belongs to another user.')
        # Owner, SYSTEM and administrators; new children inherit this private ACL.
        sddl = 'D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)'
        if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(restricted), None) or not api.SetFileSecurityW(str(path), 0x80000004, restricted):
            raise PermissionError('The application directory could not be made private.')
    finally:
        if restricted:kernel.LocalFree(restricted)
        if descriptor:kernel.LocalFree(descriptor)
        kernel.CloseHandle(token)
