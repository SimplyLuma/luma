"""Supported libflatpak transactions, with real operation progress."""
import gi
gi.require_version("Flatpak", "1.0")
from gi.repository import Flatpak, Gio, GLib
from . import network
from .errors import InstallerError
from .inspectors import architecture_name, host_architecture
from .progress import current, Aggregate, Cancelled


class _Transient(Exception):
    """A fetch that failed before anything was deployed, so it can be retried."""

    def __init__(self, cause):
        super().__init__(network.error_text(cause))
        self.cause = cause


def install(report):
    controller = current.get()
    # One install is one job. Dependency names and their individual counters
    # stay inside libflatpak; the person sees this application, once.
    label = "Installing " + (report.title or "the application")
    progress_state = {"started": 0}

    def attempt():
        progress_state["started"] = 0
        aggregate = Aggregate(1 / 3, .95)
        installation = Flatpak.Installation.new_user(None)
        cancellation = Gio.Cancellable.new()
        transaction = Flatpak.Transaction.new_for_installation(installation, cancellation)
        def ready(tx):
            operations = tx.get_operations()
            aggregate.expect(len(operations),
                             sum(op.get_download_size() or 0 for op in operations))
            return not (controller and controller.cancelled.is_set())
        def operation(tx, op, progress):
            progress_state["started"] += 1
            if controller:
                # A batch may commit individual refs before returning. Cancellation
                # is offered only before its first operation, never as a promise of
                # rollback after a runtime or application has been deployed.
                with controller.lock:
                    controller.cancellable = False
            key = op.get_ref() or id(op)
            def changed(value):
                if controller:
                    controller.report(label, aggregate.report(
                        key, value.get_progress() / 100,
                        value.get_bytes_transferred(), op.get_download_size() or 0))
            progress.connect("changed", changed)
            changed(progress)
        transaction.connect("ready", ready)
        transaction.connect("new-operation", operation)
        # Fedora's Flatpak GIR annotates the int result/details as flags, which
        # cannot be marshalled by PyGObject. We need neither signal: run() reports
        # failures and new-operation gives authoritative progress boundaries.
        # The reviewed reference/bundle is the source of the remote request.
        transaction.connect("add-new-remote", lambda *_args: True)
        transaction.connect("choose-remote-for-ref", lambda *_args: 0)
        try:
            if report.kind == "flatpak":
                transaction.add_install_bundle(Gio.File.new_for_path(str(report.path)), None)
            else:
                if report.byte_size > 1024 * 1024:
                    raise InstallerError("The Flatpak reference is too large.")
                transaction.add_install_flatpakref(GLib.Bytes.new(report.path.read_bytes()))
            if controller:
                with controller.lock:
                    controller.cancel_callback = cancellation.cancel
                    controller.cancellable = True
                    if controller.cancelled.is_set(): cancellation.cancel()
            transaction.run(cancellation)

        except GLib.Error as error:
            if cancellation.is_cancelled() and progress_state["started"] == 0:
                raise Cancelled("Installation cancelled.") from error
            if error.matches(Flatpak.error_quark(), Flatpak.Error.REF_NOT_FOUND):
                host = architecture_name(host_architecture())
                raise InstallerError(
                    f"This source has no {host} build of the requested application version. "
                    "Check the publisher’s downloads for a compatible build."
                ) from error
            # Nothing has been deployed yet, so the same fetch can simply be
            # made again. Once an operation has started, the batch is not ours
            # to replay and the failure is reported as it is.
            if progress_state["started"] == 0 and network.is_transient(error):
                raise _Transient(error) from error
            raise InstallerError(f"Flatpak could not finish: {error.message}") from error
        finally:
            if controller:
                with controller.lock:
                    controller.cancel_callback = None
                    controller.cancellable = False

    try:
        network.retrying(attempt, transient=lambda error: isinstance(error, _Transient))
    except _Transient as error:
        raise InstallerError(network.friendly(error.cause, "this application's source")) from error.cause
