# SPDX-License-Identifier: Apache-2.0
"""rpm-ostree over its D-Bus API (org.projectatomic.rpmostree1), not its CLI.

Interfaces used, as published by rpm-ostree 2026.1
(src/daemon/org.projectatomic.rpmostree1.xml):

* ``Sysroot``: ``RegisterClient``, ``Reload``, ``ReloadConfig``, ``GetOS``, and
  the ``Deployments`` and ``ActiveTransaction`` properties;
* ``OS``: ``UpdateDeployment(modifiers, options)``, ``Rollback(options)`` and
  ``KernelArgs(existing, added, replaced, removed, options)``, each returning
  the address of a private transaction bus, and ``GetDeploymentBootConfig``;
* ``Transaction`` on that private bus: ``Start``, then ``Finished``,
  ``DownloadProgress``, ``PercentProgress`` and ``Message`` signals.

``UpdateDeployment`` defaults ``allow-downgrade`` to true whenever
``set-revision`` is given, so every call here states it explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import bootmenu  # noqa: E402
from .errors import BusyError, TransactionError  # noqa: E402

__all__ = ("Deployment", "RpmOstree", "TransactionError", "BusyError", "parse_deployment")

BUS = "org.projectatomic.rpmostree1"
SYSROOT_PATH = "/org/projectatomic/rpmostree1/Sysroot"
SYSROOT_IFACE = "org.projectatomic.rpmostree1.Sysroot"
OS_IFACE = "org.projectatomic.rpmostree1.OS"
TRANSACTION_IFACE = "org.projectatomic.rpmostree1.Transaction"
CLIENT_ID = "luma-update"
DRIVER_NAME = "Luma Update"
CALL_TIMEOUT_MS = 120 * 1000
DEPLOY_ROOT = Path("/ostree/deploy")


@dataclass(frozen=True)
class Deployment:
    id: str
    osname: str
    checksum: str
    base_checksum: str
    version: str
    origin: str
    booted: bool
    staged: bool
    pinned: bool
    timestamp: int
    layered: bool
    serial: int = 0
    extra: dict = field(default_factory=dict, compare=False, repr=False)
    #: On the booted deployment: the commit whose content ``rpm-ostree apply-live``
    #: made the running system (a package added with ``dnf install``), else "".
    live_replaced: str = ""
    #: What ``rpm-ostree install`` added: repository requests and local packages (NEVRA).
    requested_packages: tuple[str, ...] = ()
    requested_local_packages: tuple[str, ...] = ()

    @property
    def base_commit(self) -> str:
        """The OSTree commit the deployment is built on (before local layering)."""
        return self.base_checksum or self.checksum


def _unpack(value):
    return value.unpack() if isinstance(value, GLib.Variant) else value


def parse_deployment(values: dict) -> Deployment:
    values = {key: _unpack(value) for key, value in values.items()}
    base_meta = values.get("base-commit-meta") or {}
    version = values.get("base-version") or values.get("version") or base_meta.get("version") or ""
    layered = bool(values.get("packages") or values.get("requested-packages")
                   or values.get("base-local-replacements") or values.get("base-removals")
                   or values.get("requested-local-packages"))
    return Deployment(
        id=str(values.get("id", "")),
        osname=str(values.get("osname", "")),
        checksum=str(values.get("checksum", "")),
        base_checksum=str(values.get("base-checksum", "")),
        version=str(version),
        origin=str(values.get("origin", "")),
        booted=bool(values.get("booted", False)),
        staged=bool(values.get("staged", False)),
        pinned=bool(values.get("pinned", False)),
        timestamp=int(values.get("timestamp", 0) or 0),
        layered=layered,
        serial=int(values.get("serial", 0) or 0),
        extra={},
        live_replaced=str(values.get("live-replaced", "") or ""),
        requested_packages=tuple(str(item) for item in values.get("requested-packages") or ()),
        requested_local_packages=tuple(str(item) for item in values.get("requested-local-packages") or ()),
    )


class RpmOstree:
    """A thin, synchronous client. Call from a worker thread, never the main loop."""

    def __init__(self, connection: Gio.DBusConnection | None = None) -> None:
        self._connection = connection
        self._registered = False
        self._lock = threading.Lock()

    @property
    def connection(self) -> Gio.DBusConnection:
        if self._connection is None:
            self._connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        return self._connection

    def _call(self, path: str, interface: str, method: str, args: GLib.Variant | None,
              reply: str | None, timeout: int = CALL_TIMEOUT_MS):
        result = self.connection.call_sync(
            BUS, path, interface, method, args,
            GLib.VariantType.new(reply) if reply else None,
            Gio.DBusCallFlags.NONE, timeout, None)
        return result.unpack() if result is not None else None

    def _property(self, path: str, interface: str, name: str):
        (value,) = self._call(path, "org.freedesktop.DBus.Properties", "Get",
                              GLib.Variant("(ss)", (interface, name)), "(v)")
        return value

    def register(self) -> None:
        with self._lock:
            if self._registered:
                return
            self._call(SYSROOT_PATH, SYSROOT_IFACE, "RegisterClient",
                       GLib.Variant("(a{sv})", ({"id": GLib.Variant("s", CLIENT_ID)},)), None)
            self._registered = True

    def unregister(self) -> None:
        with self._lock:
            if not self._registered:
                return
            try:
                self._call(SYSROOT_PATH, SYSROOT_IFACE, "UnregisterClient",
                           GLib.Variant("(a{sv})", ({},)), None)
            except GLib.Error:
                pass
            self._registered = False

    def reload(self, config: bool = False) -> None:
        self.register()
        self._call(SYSROOT_PATH, SYSROOT_IFACE, "ReloadConfig" if config else "Reload", None, None)

    def os_path(self) -> str:
        self.register()
        return self._property(SYSROOT_PATH, SYSROOT_IFACE, "Booted")

    def deployments(self) -> list[Deployment]:
        self.register()
        return [parse_deployment(item) for item in self._property(SYSROOT_PATH, SYSROOT_IFACE, "Deployments")]

    def active_transaction(self) -> tuple[str, str, str] | None:
        self.register()
        method, sender, path = self._property(SYSROOT_PATH, SYSROOT_IFACE, "ActiveTransaction")
        return (method, sender, path) if method or path else None

    def default_deployment_is_rollback(self) -> bool:
        """True when the next boot is an older, non-staged deployment (after Rollback)."""
        deployments = self.deployments()
        if not deployments:
            return False
        first = deployments[0]
        return not first.booted and not first.staged

    # ── Transactions ─────────────────────────────────────────────────────

    def update_deployment(self, *, revision: str, refspec: str | None, allow_downgrade: bool,
                          progress=None, message=None, cancellable: Gio.Cancellable | None = None,
                          register_driver: bool = True, cache_only: bool = False,
                          download_only: bool = False, no_overrides: bool = False,
                          uninstall: tuple[str, ...] = ()) -> None:
        modifiers = {"set-revision": GLib.Variant("s", revision)}
        if refspec:
            modifiers["set-refspec"] = GLib.Variant("s", refspec)
        if uninstall:
            # rpm-ostree uninstall: a repository request by name, a local package by NEVRA.
            modifiers["uninstall-packages"] = GLib.Variant("as", list(uninstall))
        options = {
            "allow-downgrade": GLib.Variant("b", bool(allow_downgrade)),
            "reboot": GLib.Variant("b", False),
            "initiating-command-line": GLib.Variant("s", "luma-updated (deploy %s)" % revision[:12]),
        }
        if register_driver:
            options["register-driver"] = GLib.Variant("s", DRIVER_NAME)
        if cache_only:
            options["cache-only"] = GLib.Variant("b", True)
        if download_only:
            options["download-only"] = GLib.Variant("b", True)
        if no_overrides:
            # Remove every base package override (rpm-ostree reset --overrides); layered packages stay.
            options["no-overrides"] = GLib.Variant("b", True)
        self._run("UpdateDeployment",
                  GLib.Variant("(a{sv}a{sv})", (modifiers, options)), progress, message, cancellable)

    def deployment_root(self, deployment: Deployment) -> Path:
        """The deployment's checked-out tree. A staged deployment is written out
        when it is staged; finalization at shutdown only adds the boot entry."""
        return DEPLOY_ROOT / deployment.osname / "deploy" / f"{deployment.checksum}.{deployment.serial}"

    def refresh_boot_menu(self, deployment: Deployment) -> str | None:
        """Bring GRUB's hidden-menu piece up to date from ``deployment``'s own
        helper (bootmenu.py). None when that release carries none."""
        return bootmenu.refresh(self.deployment_root(deployment))

    def boot_config(self, *, pending: bool = True) -> dict:
        """``GetDeploymentBootConfig`` for the pending deployment (the staged one when
        there is one, else the default), or the booted one. Keys: ``options``, ``staged``."""
        self.register()
        (config,) = self._call(self.os_path(), OS_IFACE, "GetDeploymentBootConfig",
                               GLib.Variant("(sb)", ("", bool(pending))), "(a{sv})")
        return {key: _unpack(value) for key, value in config.items()}

    def kernel_args(self, *, existing: str, append_if_missing=(), delete_if_present=(), message=None) -> None:
        """Change the pending deployment's kernel arguments. rpm-ostree appends each
        ``append_if_missing`` argument that is not already there and deletes each
        ``delete_if_present`` argument that is, then redeploys the pending
        deployment's base commit with them (no pull)."""
        options = {
            "reboot": GLib.Variant("b", False),
            "lock-finalization": GLib.Variant("b", False),
            "initiating-command-line": GLib.Variant("s", "luma-updated (kernel arguments from kargs.d)"),
        }
        if append_if_missing:
            options["append-if-missing"] = GLib.Variant("as", list(append_if_missing))
        if delete_if_present:
            options["delete-if-present"] = GLib.Variant("as", list(delete_if_present))
        self._run("KernelArgs", GLib.Variant("(sasasasa{sv})", (existing, [], [], [], options)),
                  None, message, None)

    def new_cancellable(self) -> Gio.Cancellable:
        return Gio.Cancellable.new()

    def rollback(self, *, message=None) -> None:
        self._run("Rollback", GLib.Variant("(a{sv})", ({"reboot": GLib.Variant("b", False)},)),
                  None, message, None)

    def cleanup_pending(self, *, message=None) -> None:
        """Remove the staged (pending) deployment only."""
        self._run("Cleanup", GLib.Variant("(as)", (["pending-deploy"],)), None, message, None)

    def _run(self, method: str, args: GLib.Variant, progress, message, cancellable) -> None:
        if cancellable is not None and cancellable.is_cancelled():
            raise TransactionError("the operation was cancelled before it started")
        self.register()
        if self.active_transaction() is not None:
            raise BusyError("another rpm-ostree transaction is in progress")
        os_path = self.os_path()
        try:
            (address,) = self._call(os_path, OS_IFACE, method, args, "(s)")
        except GLib.Error as error:
            text = error.message or str(error)
            if "Transaction in progress" in text:
                raise BusyError("another rpm-ostree transaction is in progress") from None
            raise TransactionError(text) from None
        _TransactionRunner(address, progress, message, cancellable).run()


class _TransactionRunner:
    def __init__(self, address: str, progress, message, cancellable) -> None:
        self.address = address
        self.progress = progress
        self.message = message
        self.cancellable = cancellable
        self.result: tuple[bool, str] | None = None

    def run(self) -> None:
        context = GLib.MainContext.new()
        context.push_thread_default()
        try:
            connection = Gio.DBusConnection.new_for_address_sync(
                self.address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT, None, None)
            loop = GLib.MainLoop.new(context, False)

            def on_signal(_conn, _sender, _path, _iface, name, params):
                values = params.unpack()
                if name == "Finished":
                    self.result = (bool(values[0]), str(values[1]))
                    loop.quit()
                elif name == "DownloadProgress" and self.progress:
                    # delta: (total parts, fetched parts, total super blocks, total size)
                    # content: (fetched, requested); transfer: (bytes, bytes/s)
                    _time, _outstanding, _metadata, delta, content, transfer = values
                    if delta[0]:
                        fraction = delta[1] / max(1, delta[0])
                    elif content[1]:
                        fraction = content[0] / max(1, content[1])
                    else:
                        fraction = None
                    self.progress(fraction, int(transfer[0]))
                elif name == "PercentProgress" and self.progress:
                    self.progress(values[1] / 100.0, None)
                elif name == "Message" and self.message:
                    self.message(str(values[0]))
                elif name == "TaskBegin" and self.message:
                    self.message(str(values[0]))

            def on_closed(_conn, _remote_peer_vanished, _error):
                if self.result is None:
                    self.result = (False, "rpm-ostree closed the transaction without finishing")
                loop.quit()

            subscription = connection.signal_subscribe(None, TRANSACTION_IFACE, None, "/", None,
                                                       Gio.DBusSignalFlags.NONE, on_signal)
            closed = connection.connect("closed", on_closed)
            cancel_id = 0
            if self.cancellable is not None:
                def on_cancel(_cancellable):
                    # Runs on the thread that cancelled (luma-updated's main loop for
                    # Cancel()), so ask without waiting; Finished(false) ends the loop.
                    connection.call(None, "/", TRANSACTION_IFACE, "Cancel", None, None,
                                    Gio.DBusCallFlags.NONE, 10000, None, None)
                cancel_id = self.cancellable.connect(on_cancel)
            try:
                connection.call_sync(None, "/", TRANSACTION_IFACE, "Start", None,
                                     GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, -1, None)
                if self.result is None:
                    loop.run()
            finally:
                connection.signal_unsubscribe(subscription)
                connection.disconnect(closed)
                if cancel_id and self.cancellable is not None:
                    self.cancellable.disconnect(cancel_id)
                try:
                    connection.close_sync(None)
                except GLib.Error:
                    pass
        except GLib.Error as error:
            raise TransactionError(error.message or str(error)) from None
        finally:
            context.pop_thread_default()
        success, text = self.result or (False, "no result")
        if not success:
            raise TransactionError(text or "rpm-ostree transaction failed")
