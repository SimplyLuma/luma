# SPDX-License-Identifier: MPL-2.0
"""Checking a login password with PAM, through libpam.

The repair re-keys the keyring to the person's current login password. If
what they typed is not that password, the keyring would be locked out again
at the next login and they would be no better off, so it is checked first --
against PAM, the same way the lock screen checks it, using the account's own
stack (`/etc/pam.d/luma-keyring`).

A user's process can do this because pam_unix asks the setuid unix_chkpwd
helper on its behalf. When PAM cannot be used at all, the caller is told so
rather than being allowed to assume the password was right.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import getpass
import logging

__all__ = ("PamUnavailable", "check_login_password")

log = logging.getLogger("luma-keyring")

SERVICE = "luma-keyring"
PAM_SUCCESS = 0
PAM_PROMPT_ECHO_OFF = 1
PAM_PROMPT_ECHO_ON = 2


class PamUnavailable(Exception):
    """PAM could not be asked; nothing is claimed about the password."""


class _PamMessage(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class _PamResponse(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_char_p), ("resp_retcode", ctypes.c_int)]


_CONV_FUNC = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(_PamMessage)),
    ctypes.POINTER(ctypes.POINTER(_PamResponse)), ctypes.c_void_p)


class _PamConv(ctypes.Structure):
    _fields_ = [("conv", _CONV_FUNC), ("appdata_ptr", ctypes.c_void_p)]


def _library() -> ctypes.CDLL:
    name = ctypes.util.find_library("pam")
    if not name:
        raise PamUnavailable("libpam is not installed")
    try:
        return ctypes.CDLL(name)
    except OSError as error:
        raise PamUnavailable(str(error)) from None


def check_login_password(password: str, username: str | None = None) -> bool:
    """Whether this is the person's current login password."""
    pam = _library()
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    libc.calloc.restype = ctypes.c_void_p
    libc.strdup.restype = ctypes.c_void_p

    username = username or getpass.getuser()
    secret = password.encode("utf-8")

    def conversation(n_messages, messages, response, _appdata):
        array = libc.calloc(n_messages, ctypes.sizeof(_PamResponse))
        if not array:
            return 5  # PAM_BUF_ERR
        responses = ctypes.cast(array, ctypes.POINTER(_PamResponse))
        for i in range(n_messages):
            style = messages[i].contents.msg_style
            if style in (PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON):
                responses[i].resp = ctypes.cast(libc.strdup(secret), ctypes.c_char_p)
            responses[i].resp_retcode = 0
        response[0] = responses
        return PAM_SUCCESS

    handle = ctypes.c_void_p()
    conv = _PamConv(_CONV_FUNC(conversation), None)
    started = pam.pam_start(SERVICE.encode(), username.encode(),
                            ctypes.byref(conv), ctypes.byref(handle))
    if started != PAM_SUCCESS:
        raise PamUnavailable(f"pam_start returned {started}")
    try:
        result = pam.pam_authenticate(handle, 0)
    finally:
        pam.pam_end(handle, result if "result" in dir() else 0)

    if result == PAM_SUCCESS:
        return True
    # 7 is PAM_AUTH_ERR: the wrong password, which is an answer, not a
    # failure to ask.
    if result == 7:
        return False
    raise PamUnavailable(f"pam_authenticate returned {result}")
