# SPDX-License-Identifier: Apache-2.0
"""Reviewed per-app channel changes using libflatpak rebase transactions.

A channel belongs to an installed app, independently of the OS channel. This
uses Flatpak.Transaction.add_rebase_and_uninstall (Flatpak >=1.15.4), retains
its app ID/data, and refuses a different signed data-schema generation.
https://docs.flatpak.org/en/latest/libflatpak-api-reference.html
"""
from dataclasses import dataclass
import configparser
import re
from .depot_flatpak import (SourceUnavailable, installed_ref, metadata_text,
    require_update_baseline, resolve, validate_remote)
from .depot_permissions import from_metadata, diff

CHANNELS = frozenset({'beta', 'nightly'})
SCHEMA = re.compile(r'[a-z][a-z0-9.-]{0,63}\Z')

@dataclass(frozen=True)
class ChannelReview:
    source: object
    old_ref: str
    old_commit: str
    schema: str
    permission_changes: tuple


def data_schema(text):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(text)
        value = parser.get('X-Luma', 'app-data-schema', fallback='')
    except configparser.Error as error:
        raise SourceUnavailable('This application has no checked data compatibility declaration.') from error
    if not SCHEMA.fullmatch(value):
        raise SourceUnavailable('This application has no checked data compatibility declaration.')
    return value


def review(installation, old, branch, cancellable=None):
    if old.get_origin() != 'luma' or branch not in CHANNELS or branch == old.get_branch():
        raise SourceUnavailable('That application update channel is unavailable.')
    validate_remote(installation.get_remote_by_name('luma', cancellable), 'luma')
    source = resolve(installation, old.get_name(), old.get_arch(), cancellable, 'luma', branch)
    from gi.repository import Flatpak
    remote = installation.fetch_remote_ref_sync('luma', Flatpak.RefKind.APP,
                                                old.get_name(), old.get_arch(), branch, cancellable)
    if remote.get_commit() != source.commit or remote.format_ref() != source.ref:
        raise SourceUnavailable('The application changed. Review its channel again.')
    before = metadata_text(old.load_metadata(cancellable))
    payload = remote.get_metadata()
    if payload is None or not payload.get_size():
        raise SourceUnavailable('The signed target metadata is unavailable.')
    after = metadata_text(payload)
    from .app_host_compatibility import require_host_compatibility
    require_host_compatibility(after)
    schema = data_schema(before)
    if data_schema(after) != schema:
        raise SourceUnavailable('These channels use different document formats. Keep this channel to preserve your data.')
    changes = tuple(diff(from_metadata(before, old.get_name(), strict=True),
                         from_metadata(after, old.get_name(), strict=True)))
    return ChannelReview(source, old.format_ref(), old.get_commit(), schema, changes)


def operations_match(reviewed, operations):
    from gi.repository import Flatpak
    target = [op for op in operations if op.get_ref() == reviewed.source.ref]
    removal = [op for op in operations if op.get_ref() == reviewed.old_ref]
    if len(target) != 1 or len(removal) != 1:
        return False
    if (target[0].get_commit() != reviewed.source.commit or target[0].get_remote() != 'luma'
            or target[0].get_operation_type() != Flatpak.TransactionOperationType.INSTALL
            or removal[0].get_operation_type() != Flatpak.TransactionOperationType.UNINSTALL):
        return False
    for op in operations:
        if op in (target[0], removal[0]): continue
        # No unrelated application installs/removals can hide in a rebase.
        parts = op.get_ref().split('/')
        if (len(parts) != 4 or parts[0] != 'runtime'
                or parts[1] not in {'org.projectluma.Platform', 'org.projectluma.Platform.Locale',
                                    'org.projectluma.Platform.GL.default', 'org.projectluma.Platform.Office'}
                or parts[2] != reviewed.source.architecture or parts[3] != '44'
                or op.get_remote() != 'luma'
                or op.get_operation_type() not in (Flatpak.TransactionOperationType.INSTALL,
                                                   Flatpak.TransactionOperationType.UPDATE)):
            return False
    return True


def rollback_operations_match(ref, commit, before, operations):
    """Bind a first-party rollback to one app and its signed data generation.

    Prepared libflatpak metadata is the exact downloaded target, not the
    remote's latest branch metadata (which may be a different commit).
    """
    from gi.repository import Flatpak
    from .depot_permissions import widens
    own = [op for op in operations if op.get_ref() == ref.format_ref()]
    if (len(own) != 1 or own[0].get_commit() != commit
            or own[0].get_remote() != 'luma'
            or own[0].get_operation_type() != Flatpak.TransactionOperationType.UPDATE):
        return False
    metadata = own[0].get_metadata()
    if metadata is None:
        return False
    after = metadata.to_data()[0]
    from .app_host_compatibility import require_host_compatibility
    require_host_compatibility(after)
    if data_schema(before) != data_schema(after):
        return False
    if widens(diff(from_metadata(before, ref.get_name(), strict=True),
                   from_metadata(after, ref.get_name(), strict=True))):
        # The existing Go back action has no new-permission review sheet.
        # Do not treat consent to downgrade as consent to broader access.
        return False
    for op in operations:
        if op is own[0]:
            continue
        parts = op.get_ref().split('/')
        if (len(parts) != 4 or parts[0] != 'runtime'
                or parts[1] not in {'org.projectluma.Platform', 'org.projectluma.Platform.Locale',
                                    'org.projectluma.Platform.GL.default', 'org.projectluma.Platform.Office'}
                or parts[2] != ref.get_arch() or parts[3] != '44'
                or op.get_remote() != 'luma'
                or op.get_operation_type() not in (Flatpak.TransactionOperationType.INSTALL,
                                                   Flatpak.TransactionOperationType.UPDATE)):
            return False
    return True


def transaction(installation, reviewed, cancellable=None):
    from gi.repository import Flatpak, GLib
    old = next((item for item in installation.list_installed_refs(cancellable)
                if item.format_ref() == reviewed.old_ref), None)
    require_update_baseline(old, reviewed.old_commit)
    if old.get_origin() != 'luma' or review(installation, old, reviewed.source.branch, cancellable) != reviewed:
        raise SourceUnavailable('The application changed. Review its channel again.')
    if installed_ref(installation, reviewed.source, cancellable) is not None:
        raise SourceUnavailable('That app channel is already installed. Remove the duplicate before switching.')
    tx = Flatpak.Transaction.new_for_installation(installation, cancellable)
    tx.connect('add-new-remote', lambda *_: False)
    def ready(prepared):
        try:
            current = next((item for item in installation.list_installed_refs(cancellable)
                            if item.format_ref() == reviewed.old_ref), None)
            require_update_baseline(current, reviewed.old_commit)
            if current.get_origin() != 'luma': return False
            if not operations_match(reviewed, prepared.get_operations()): return False
            validate_remote(installation.get_remote_by_name('luma', cancellable), 'luma')
        except (SourceUnavailable, GLib.Error): return False
        return True
    tx.connect('ready', ready)
    tx.add_rebase_and_uninstall('luma', reviewed.source.ref, reviewed.old_ref, None, None)
    return tx
