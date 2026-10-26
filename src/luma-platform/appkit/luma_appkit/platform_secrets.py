# SPDX-License-Identifier: Apache-2.0
"""Credential backends behind ``luma_appkit.platform.secret_store``.

Every backend answers the same three calls, keyed by an opaque account id and a
credential kind (``"password"``, ``"oauth-token"``...):

    store(account, kind, value, label=None)
    lookup(account, kind) -> str | None
    clear(account, kind) -> bool

Secret values are handed to the platform store in-process (libsecret, the Security
framework, advapi32). They never go into process arguments, logs or files.
"""
from __future__ import annotations

import ctypes
import ctypes.util
from typing import Protocol


class SecretBackend(Protocol):
    def store(self, account: str, kind: str, value: str, label: str | None = None) -> None: ...
    def lookup(self, account: str, kind: str) -> str | None: ...
    def clear(self, account: str, kind: str) -> bool: ...


class MemorySecretStore:
    """In-process only: fixtures, tests, and platforms without a store yet."""

    name = "memory"

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], str] = {}

    def store(self, account: str, kind: str, value: str, label: str | None = None) -> None:
        self._items[(account, kind)] = value

    def lookup(self, account: str, kind: str) -> str | None:
        return self._items.get((account, kind))

    def clear(self, account: str, kind: str) -> bool:
        return self._items.pop((account, kind), None) is not None


class LibsecretStore:
    """Secret Service through libsecret (Linux, Luma OS)."""

    name = "libsecret"

    def __init__(self, app_id: str) -> None:
        import gi

        gi.require_version("Secret", "1")
        from gi.repository import Secret

        self.Secret = Secret
        self.app_id = app_id
        self.schema = Secret.Schema.new(
            app_id,
            Secret.SchemaFlags.NONE,
            {"account": Secret.SchemaAttributeType.STRING, "kind": Secret.SchemaAttributeType.STRING},
        )

    def store(self, account: str, kind: str, value: str, label: str | None = None) -> None:
        self.Secret.password_store_sync(
            self.schema, {"account": account, "kind": kind}, self.Secret.COLLECTION_DEFAULT,
            label or f"{self.app_id} {kind} for {account}", value, None,
        )

    def lookup(self, account: str, kind: str) -> str | None:
        return self.Secret.password_lookup_sync(self.schema, {"account": account, "kind": kind}, None)

    def clear(self, account: str, kind: str) -> bool:
        return bool(self.Secret.password_clear_sync(self.schema, {"account": account, "kind": kind}, None))


class KeychainStore:
    """The macOS login Keychain: generic passwords, service = app id, account = "<account>/<kind>".

    Uses the Security framework's generic-password calls through ctypes, so the secret
    stays in this process (the ``security`` CLI would need it on its command line)."""

    name = "keychain"
    _NOT_FOUND = -25300
    _DUPLICATE = -25299

    def __init__(self, app_id: str) -> None:
        self.service = app_id.encode()
        sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        u32, vp, cp = ctypes.c_uint32, ctypes.c_void_p, ctypes.c_char_p
        sec.SecKeychainAddGenericPassword.argtypes = [vp, u32, cp, u32, cp, u32, cp, ctypes.POINTER(vp)]
        sec.SecKeychainFindGenericPassword.argtypes = [vp, u32, cp, u32, cp, ctypes.POINTER(u32),
                                                       ctypes.POINTER(vp), ctypes.POINTER(vp)]
        sec.SecKeychainItemModifyAttributesAndData.argtypes = [vp, vp, u32, cp]
        sec.SecKeychainItemFreeContent.argtypes = [vp, vp]
        sec.SecKeychainItemDelete.argtypes = [vp]
        for fn in (sec.SecKeychainAddGenericPassword, sec.SecKeychainFindGenericPassword,
                   sec.SecKeychainItemModifyAttributesAndData, sec.SecKeychainItemFreeContent,
                   sec.SecKeychainItemDelete):
            fn.restype = ctypes.c_int32
        cf.CFRelease.argtypes = [vp]
        cf.CFRelease.restype = None
        self._sec, self._cf = sec, cf

    def _find(self, account: str, kind: str, want_data: bool):
        key = f"{account}/{kind}".encode()
        length = ctypes.c_uint32(0)
        data = ctypes.c_void_p()
        item = ctypes.c_void_p()
        status = self._sec.SecKeychainFindGenericPassword(
            None, len(self.service), self.service, len(key), key,
            ctypes.byref(length) if want_data else None, ctypes.byref(data) if want_data else None,
            ctypes.byref(item))
        if status == self._NOT_FOUND:
            return None, None
        if status != 0:
            raise OSError(f"Keychain lookup failed (OSStatus {status})")
        value = None
        if want_data and data.value:
            value = ctypes.string_at(data.value, length.value).decode("utf-8", "replace")
            self._sec.SecKeychainItemFreeContent(None, data)
        return item, value

    def store(self, account: str, kind: str, value: str, label: str | None = None) -> None:
        key = f"{account}/{kind}".encode()
        secret = value.encode()
        item, _ = self._find(account, kind, want_data=False)
        if item is not None:
            try:
                status = self._sec.SecKeychainItemModifyAttributesAndData(item, None, len(secret), secret)
            finally:
                self._cf.CFRelease(item)
        else:
            status = self._sec.SecKeychainAddGenericPassword(
                None, len(self.service), self.service, len(key), key, len(secret), secret, None)
        if status != 0:
            raise OSError(f"Keychain store failed (OSStatus {status})")

    def lookup(self, account: str, kind: str) -> str | None:
        item, value = self._find(account, kind, want_data=True)
        if item is not None:
            self._cf.CFRelease(item)
        return value

    def clear(self, account: str, kind: str) -> bool:
        item, _ = self._find(account, kind, want_data=False)
        if item is None:
            return False
        try:
            return self._sec.SecKeychainItemDelete(item) == 0
        finally:
            self._cf.CFRelease(item)


class WindowsCredentialStore:
    """Windows Credential Manager: generic credentials named "<app id>/<account>/<kind>"."""

    name = "wincred"
    _GENERIC = 1
    _PERSIST_LOCAL_MACHINE = 2
    _NOT_FOUND = 1168

    def __init__(self, app_id: str) -> None:
        from ctypes import wintypes as w

        class FILETIME(ctypes.Structure):
            _fields_ = [("lo", w.DWORD), ("hi", w.DWORD)]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [("Flags", w.DWORD), ("Type", w.DWORD), ("TargetName", w.LPWSTR),
                        ("Comment", w.LPWSTR), ("LastWritten", FILETIME),
                        ("CredentialBlobSize", w.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", w.DWORD), ("AttributeCount", w.DWORD), ("Attributes", ctypes.c_void_p),
                        ("TargetAlias", w.LPWSTR), ("UserName", w.LPWSTR)]

        self.app_id = app_id
        self._CRED = CREDENTIALW
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        api.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), w.DWORD]
        api.CredReadW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
        api.CredDeleteW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD]
        api.CredFree.argtypes = [ctypes.c_void_p]
        for fn in (api.CredWriteW, api.CredReadW, api.CredDeleteW):
            fn.restype = w.BOOL
        self._api = api

    def _target(self, account: str, kind: str) -> str:
        return f"{self.app_id}/{account}/{kind}"

    _TEXT = b'\0LumaText1:'
    _PROTECTED = b'\0LumaDPAPI1:'

    def _write(self, account, kind, blob, label):
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = self._CRED(Type=self._GENERIC, TargetName=self._target(account, kind), Comment=label,
                          CredentialBlobSize=len(blob), CredentialBlob=buf,
                          Persist=self._PERSIST_LOCAL_MACHINE, UserName=account)
        if not self._api.CredWriteW(ctypes.byref(cred), 0):
            raise OSError(f"Credential Manager store failed ({ctypes.get_last_error()})")

    def _read(self, account, kind):
        out = ctypes.POINTER(self._CRED)()
        if not self._api.CredReadW(self._target(account, kind), self._GENERIC, 0, ctypes.byref(out)):
            if ctypes.get_last_error() == self._NOT_FOUND:return None
            raise OSError(f"Credential Manager lookup failed ({ctypes.get_last_error()})")
        try:
            value = out.contents
            return ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize)
        finally:self._api.CredFree(out)

    def _directory(self):
        import os, hashlib
        from pathlib import Path
        from .private_paths import ensure_private_directory
        base = os.environ.get('LOCALAPPDATA')
        if not base or not Path(base).is_absolute():raise OSError('The private credential directory is unavailable')
        directory = Path(base)/'Luma'/'Credentials'/hashlib.sha256(self.app_id.encode()).hexdigest()
        ensure_private_directory(directory)
        return directory

    def _protected_path(self, record):
        if record is None or not record.startswith(self._PROTECTED):return None
        digest = record[len(self._PROTECTED):].decode('ascii')
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise OSError('Invalid protected credential reference')
        return self._directory()/digest

    def _crypt(self, data, account, kind, *, decrypt=False):
        import hashlib
        from ctypes import wintypes as w
        class Blob(ctypes.Structure):
            _fields_=[('length',w.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
        def blob(value):
            buffer=(ctypes.c_ubyte*len(value)).from_buffer_copy(value)
            return Blob(len(value),buffer),buffer
        value, value_buffer = blob(data)
        entropy, entropy_buffer = blob(hashlib.sha256(self._target(account,kind).encode('utf-8')).digest())
        result=Blob()
        api=ctypes.WinDLL('crypt32',use_last_error=True)
        function=api.CryptUnprotectData if decrypt else api.CryptProtectData
        function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,w.DWORD,ctypes.POINTER(Blob)]
        function.restype=w.BOOL
        if not function(ctypes.byref(value),None,ctypes.byref(entropy),None,None,1,ctypes.byref(result)):
            raise OSError('Windows could not protect this credential')
        kernel=ctypes.WinDLL('kernel32');kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
        try:
            if result.length>128*1024:raise OSError('Protected credential is too large')
            return ctypes.string_at(result.data,result.length)
        finally:kernel.LocalFree(result.data)

    def store(self, account: str, kind: str, value: str, label: str | None = None) -> None:
        import hashlib, os, tempfile
        encoded=value.encode('utf-8')
        if len(encoded)>65536:raise OSError('Credential is too large')
        previous=self._protected_path(self._read(account,kind))
        created=None
        record=self._TEXT+encoded
        if len(record)>2560:
            cipher=self._crypt(encoded,account,kind)
            digest=hashlib.sha256(cipher).hexdigest()
            created=self._directory()/digest
            descriptor, temporary=tempfile.mkstemp(prefix='.credential-',dir=created.parent)
            try:
                with os.fdopen(descriptor,'wb') as output:
                    output.write(cipher);output.flush();os.fsync(output.fileno())
                os.replace(temporary,created)
            finally:
                from pathlib import Path
                Path(temporary).unlink(missing_ok=True)
            record=self._PROTECTED+digest.encode('ascii')
        try:self._write(account,kind,record,label)
        except BaseException:
            if created:created.unlink(missing_ok=True)
            raise
        if previous and previous!=created:
            try:previous.unlink(missing_ok=True)
            except OSError:pass  # The credential commit has already completed.

    def lookup(self, account: str, kind: str) -> str | None:
        import hashlib
        record=self._read(account,kind)
        if record is None:return None
        path=self._protected_path(record)
        if path is not None:
            info=path.lstat()
            if not path.is_file() or getattr(info,'st_file_attributes',0)&0x400 or info.st_size>128*1024:
                raise OSError('The protected credential is invalid')
            with path.open('rb') as stream:cipher=stream.read(128*1024+1)
            if hashlib.sha256(cipher).hexdigest()!=path.name:raise OSError('The protected credential is invalid')
            return self._crypt(cipher,account,kind,decrypt=True).decode('utf-8')
        if record.startswith(self._TEXT):record=record[len(self._TEXT):]
        return record.decode('utf-8')

    def clear(self, account: str, kind: str) -> bool:
        path=self._protected_path(self._read(account,kind))
        if self._api.CredDeleteW(self._target(account,kind),self._GENERIC,0):
            if path:path.unlink(missing_ok=True)
            return True
        code=ctypes.get_last_error()
        if code==self._NOT_FOUND:return False
        raise OSError(f"Credential Manager removal failed ({code})")


def open_secret_store(app_id: str, system: str) -> SecretBackend:
    """The platform's store, or memory when it cannot be reached (said once on stderr)."""
    if system == "android":
        from .android_secrets import AndroidCredentialBackend
        return AndroidCredentialBackend(app_id)
    try:
        if system == "linux":
            return LibsecretStore(app_id)
        if system == "macos":
            return KeychainStore(app_id)
        if system == "windows":
            return WindowsCredentialStore(app_id)
        if system == "android":
            from .android_secrets import AndroidCredentialBackend
            return AndroidCredentialBackend(app_id)
    except (ImportError, ValueError, OSError, AttributeError) as error:
        import sys

        print(f"luma_appkit: no {system} secret store ({error}); credentials kept in memory",
              file=sys.stderr)
    return MemorySecretStore()
