"""Adaptive graphical catalog and lifecycle surface for Luma Mods."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from luma_appkit import AppWindow, CommandRegistry, EmptyState, Island, TabBar

from .catalog_runtime import (
    SYSTEM_CLIENT_CONFIG,
    CatalogClientConfig,
    TrustedCatalogRuntime,
    user_catalog_cache,
)
from .errors import LumaModsError
from .review import IMPACT_LABELS, ReviewWindow
from .runtime import UserRuntime, load_catalog, profile_for, verify_plan
from .resolver import Catalog, resolve_mod
from .trust import TrustPolicy
from .system_client import SystemTransactionClient


def _clear(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


class ModsWindow(AppWindow):
    def __init__(
        self,
        application: Adw.Application,
        root: Path,
        catalog: Catalog,
        runtime: UserRuntime,
        policy: TrustPolicy | None,
        trusted_catalog: TrustedCatalogRuntime | None = None,
    ) -> None:
        super().__init__(application=application, app_id="org.projectluma.Mods", title="Mods", icon_name="org.projectluma.Mods", commands=CommandRegistry(()), default_width=880, default_height=720, minimum_width=340, minimum_height=480)
        self._root = root
        self._catalog = catalog
        self._runtime = runtime
        self._policy = policy
        self._trusted_catalog = trusted_catalog
        self._query = ""
        self._discover_results: Gtk.Box | None = None

        self._stack = Adw.ViewStack()
        self._pages: dict[str, Gtk.Box] = {}
        for name, title, icon in (
            ("discover", "Discover", "system-search-symbolic"),
            ("installed", "Installed", "emblem-ok-symbolic"),
            ("updates", "Updates", "software-update-available-symbolic"),
            ("history", "History", "document-open-recent-symbolic"),
        ):
            page, content = self._page_box()
            self._pages[name] = content
            stack_page = self._stack.add_titled(page, name, title)
            stack_page.set_icon_name(icon)

        toolbar = Adw.ToolbarView()
        toolbar.set_vexpand(True)
        self._stack.set_vexpand(True)
        toolbar.set_content(self._stack)
        self._tabs = TabBar([
            ("discover", "Discover", "search"),
            ("installed", "Installed", "circle-check"),
            ("updates", "Updates", "refresh-cw"),
            ("history", "History", "history"),
        ], current="discover", on_change=self._stack.set_visible_child_name)
        toolbar.add_bottom_bar(self._tabs)
        self._toasts = Adw.ToastOverlay(child=toolbar)
        self._toasts.set_vexpand(True)
        island = Island()
        island.append(self._toasts)
        self.set_body(island)
        self.refresh()

    @staticmethod
    def _page_box() -> tuple[Gtk.Box, Gtk.Box]:
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=24,
            margin_bottom=24,
            margin_start=18,
            margin_end=18,
        )
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_vexpand(True)
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=560, child=box)
        scroller.set_child(clamp)
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        wrapper.append(scroller)
        return wrapper, box

    def _content(self, name: str) -> Gtk.Box:
        return self._pages[name]

    def refresh(self) -> None:
        for page in self._pages.values():
            _clear(page)
        state = self._runtime.store.read()
        self._fill_discover(state)
        self._fill_installed(state)
        self._fill_updates(state)
        self._fill_history(state)

    def _heading(self, box: Gtk.Box, title: str, subtitle: str) -> None:
        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class("title-1")
        box.append(label)
        description = Gtk.Label(label=subtitle, xalign=0, wrap=True)
        description.add_css_class("dim-label")
        box.append(description)

    def _catalog_row(self, inspection, installed: dict) -> Adw.ActionRow:
        identity = inspection.mod.identity
        row = Adw.ActionRow(
            title=identity.name,
            subtitle=(
                f"{IMPACT_LABELS.get(identity.kind, identity.kind.title())} · "
                f"{identity.publisher.name}\n{identity.summary}"
            ),
            activatable=True,
        )
        row.add_prefix(Gtk.Image(icon_name="application-x-addon-symbolic"))
        record = installed.get(identity.id)
        if record:
            status = Gtk.Label(label="On" if record["enabled"] else "Off")
            status.add_css_class("dim-label")
            row.add_suffix(status)
        if self._trusted_catalog is not None and self._trusted_catalog.snapshot is not None:
            role = self._trusted_catalog.snapshot.entries[identity.id].trust_role
            role_label = {
                "luma-core": "Luma Core catalog",
                "luma-verified": "Luma Verified catalog",
                "community": "Community catalog",
            }[role]
            catalog_status = Gtk.Label(label=role_label)
            catalog_status.add_css_class("dim-label")
            row.add_suffix(catalog_status)
        arrow = Gtk.Image(icon_name="go-next-symbolic")
        row.add_suffix(arrow)
        row.connect("activated", lambda _row: self._open_review(identity.id))
        return row

    def _fill_discover(self, state: dict) -> None:
        box = self._content("discover")
        self._heading(
            box,
            "Make Luma yours",
            "Mods are reviewed, bounded changes that Luma can explain and undo.",
        )
        search = Gtk.SearchEntry(placeholder_text="Search Mods")
        search.set_text(self._query)
        search.connect("search-changed", self._search_changed)
        box.append(search)
        self._discover_results = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
        )
        box.append(self._discover_results)
        self._rebuild_discover_results(state)

    def _search_changed(self, search: Gtk.SearchEntry) -> None:
        self._query = search.get_text().strip().casefold()
        self._rebuild_discover_results(self._runtime.store.read())

    def _rebuild_discover_results(self, state: dict) -> None:
        results = self._discover_results
        if results is None:
            return
        _clear(results)
        group = Adw.PreferencesGroup()
        count = 0
        for inspection in self._catalog.inspections():
            if inspection.mod.identity.id.endswith(".dock-style-api"):
                continue
            identity = inspection.mod.identity
            haystack = " ".join((
                identity.name,
                identity.summary,
                identity.publisher.name,
                identity.publisher.id,
                identity.kind,
                identity.id,
            )).casefold()
            if self._query and self._query not in haystack:
                continue
            group.add(self._catalog_row(inspection, state["installed"]))
            count += 1
        if count:
            results.append(group)
        else:
            results.append(
                Adw.StatusPage(
                    title="No matching Mods",
                    icon_name="system-search-symbolic",
                )
            )

    def _fill_installed(self, state: dict) -> None:
        box = self._content("installed")
        self._heading(box, "Installed", "Active, disabled, and shared dependency Mods.")
        if not state["installed"]:
            box.append(Adw.StatusPage(title="No Mods installed", icon_name="application-x-addon-symbolic"))
            return
        group = Adw.PreferencesGroup()
        for identifier, record in sorted(state["installed"].items()):
            try:
                inspection = self._catalog.require(identifier)
                row = self._catalog_row(inspection, state["installed"])
            except LumaModsError:
                row = Adw.ActionRow(title=identifier, subtitle="Installed catalog entry unavailable")
            version = Gtk.Label(label=record["version"])
            version.add_css_class("dim-label")
            row.add_suffix(version)
            group.add(row)
        box.append(group)

    def _fill_updates(self, state: dict) -> None:
        box = self._content("updates")
        self._heading(
            box,
            "Updates",
            "Envelope-compatible updates can be automatic; broader effects require review.",
        )
        available = []
        for identifier, record in sorted(state["installed"].items()):
            try:
                inspection = self._catalog.require(identifier)
            except LumaModsError:
                continue
            if inspection.mod.identity.version != record["version"]:
                available.append(inspection)
        if not available:
            box.append(Adw.StatusPage(title="Mods are up to date", icon_name="emblem-ok-symbolic"))
            return
        group = Adw.PreferencesGroup()
        for inspection in available:
            group.add(self._catalog_row(inspection, state["installed"]))
        box.append(group)

    def _fill_history(self, state: dict) -> None:
        box = self._content("history")
        self._heading(box, "History & Recovery", "Every committed or recovered change is retained here.")
        if state["pending_transaction"] is not None:
            pending = state["pending_transaction"]
            group = Adw.PreferencesGroup(title="Interrupted change")
            row = Adw.ActionRow(
                title=pending["target_id"],
                subtitle=(
                    f"{pending['operation'].title()} stopped during {pending['phase']}. "
                    "Luma will restore the prior state."
                ),
            )
            recover = Gtk.Button(label="Recover", valign=Gtk.Align.CENTER)
            recover.add_css_class("suggested-action")
            recover.connect("clicked", self._recover)
            row.add_suffix(recover)
            group.add(row)
            box.append(group)
        if not state["audit"]:
            box.append(Adw.StatusPage(title="No Mod changes yet", icon_name="document-open-recent-symbolic"))
            return
        group = Adw.PreferencesGroup()
        for entry in reversed(state["audit"]):
            row = Adw.ActionRow(
                title=f"{entry['operation'].title()} · {entry['target_id']}",
                subtitle=(
                    f"{entry['result'].title()} · generation "
                    f"{entry['from_generation']} → {entry['to_generation']} · {entry['at']}"
                ),
            )
            group.add(row)
        box.append(group)

    def _recover(self, _button) -> None:
        try:
            self._runtime.lifecycle.recover()
        except (LumaModsError, OSError) as error:
            self._toasts.add_toast(Adw.Toast(title=str(error), timeout=5))
            return
        self._toasts.add_toast(Adw.Toast(title="Previous state restored", timeout=3))
        self.refresh()

    def _open_review(self, identifier: str) -> None:
        if hasattr(self._runtime, 'review'):
            try:
                plan, verifications, profile = self._runtime.review(identifier,self._catalog)
            except (LumaModsError, OSError) as error:
                self._toasts.add_toast(Adw.Toast(title=str(error),timeout=5)); return
            system_stage = system_activate = None
            if profile is None and self._runtime.system_snapshot and any(p.inspection.mod.payloads for p in plan.mods):
                client_holder = {}
                def system_client():
                    if "client" not in client_holder:
                        client_holder["client"] = SystemTransactionClient()
                    return client_holder["client"]
                snapshot = self._runtime.system_snapshot
                def system_stage(callback):
                    try: system_client().stage_catalog(identifier,snapshot,plan.composition_sha256,callback)
                    except (LumaModsError,OSError) as error: callback(None,error)
                def system_activate(candidate,callback):
                    try: system_client().activate(candidate,callback)
                    except (LumaModsError,OSError) as error: callback(None,error)
            window = ReviewWindow(self.get_application(),plan,verifications,
                runtime=self._runtime if profile is not None else None,profile=profile,
                changed_callback=self.refresh,system_stage=system_stage,system_activate=system_activate)
            window.set_transient_for(self); window.present(); return
        try:
            if self._trusted_catalog is not None:
                trusted = self._trusted_catalog.plan(
                    identifier, self._runtime.host_context()
                )
                plan = trusted.plan
                verifications = dict(trusted.verifications)
            else:
                trusted = None
                plan = resolve_mod(identifier, self._catalog, self._runtime.host_context())
                verifications = verify_plan(plan, self._policy)
        except (LumaModsError, OSError) as error:
            self._toasts.add_toast(Adw.Toast(title=str(error), timeout=5))
            return
        if self._trusted_catalog is not None:
            try:
                profile = self._trusted_catalog.profile(identifier)
            except LumaModsError:
                profile = None
        else:
            try:
                profile = profile_for(self._root, identifier)
            except LumaModsError:
                profile = None
        catalog_transaction = None
        if trusted is not None and profile is not None:
            reviewed_composition = trusted.plan.composition_sha256
            reviewed_profile = profile.source_sha256

            def catalog_transaction():
                return self._trusted_catalog.prepare_transaction(
                    identifier,
                    self._runtime.host_context(),
                    reviewed_composition_sha256=reviewed_composition,
                    reviewed_profile_sha256=reviewed_profile,
                )
        system_stage = None
        system_activate = None
        has_system_payload = any(
            planned.inspection.mod.payloads for planned in plan.mods
        )
        if trusted is not None and profile is None and has_system_payload:
            reviewed_snapshot = trusted.snapshot_id
            reviewed_composition = trusted.plan.composition_sha256
            client_holder: dict[str, SystemTransactionClient] = {}

            def system_client() -> SystemTransactionClient:
                if "client" not in client_holder:
                    client_holder["client"] = SystemTransactionClient()
                return client_holder["client"]

            def system_stage(callback):
                try:
                    system_client().stage_catalog(
                        identifier,
                        reviewed_snapshot,
                        reviewed_composition,
                        callback,
                    )
                except (LumaModsError, OSError) as error:
                    callback(None, error)

            def system_activate(candidate, callback):
                try:
                    system_client().activate(candidate, callback)
                except (LumaModsError, OSError) as error:
                    callback(None, error)
        window = ReviewWindow(
            self.get_application(),
            plan,
            verifications,
            runtime=self._runtime if profile is not None else None,
            profile=profile,
            policy=self._policy,
            changed_callback=self.refresh,
            trusted_plan=trusted,
            catalog_transaction=catalog_transaction,
            system_stage=system_stage,
            system_activate=system_activate,
        )
        window.set_transient_for(self)
        window.present()


class ModsApplication(Adw.Application):
    def __init__(
        self,
        root: Path,
        catalog: Catalog,
        policy: TrustPolicy | None,
        trusted_catalog: TrustedCatalogRuntime | None = None,
        runtime=None,
        startup_error=None,
    ) -> None:
        super().__init__(application_id="org.projectluma.Mods")
        self.root = root
        self.catalog = catalog
        self.policy = policy
        self.runtime = runtime if runtime is not None else (None if startup_error else UserRuntime.current())
        self.startup_error = startup_error
        self.trusted_catalog = trusted_catalog

    def do_activate(self) -> None:
        try:
            if self.startup_error: raise LumaModsError(self.startup_error)
            window = self.props.active_window or ModsWindow(self,self.root,self.catalog,self.runtime,self.policy,self.trusted_catalog)
        except (LumaModsError, OSError) as error:
            window = AppWindow(application=self,app_id='org.projectluma.Mods',title='Mods',icon_name='org.projectluma.Mods',commands=CommandRegistry(()),default_width=880,default_height=720,minimum_width=340,minimum_height=480)
            window.set_body(EmptyState('Mods is unavailable',str(error),'dialog-information-symbolic'))
        window.present()


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browse and manage Luma Mods.")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--catalog-config", type=Path)
    parser.add_argument("--trust-policy", type=Path)
    args = parser.parse_args(arguments)
    try:
        if Path('/.flatpak-info').is_file():
            if args.catalog or args.catalog_config or args.trust_policy:
                raise LumaModsError('The installed Mods app uses the host catalog. Custom inspection remains available through the native luma-mod tool.')
            from .profile_client import Client
            runtime = Client()
            root, catalog = runtime.catalog()
            application = ModsApplication(root,catalog,None,runtime=runtime)
            return application.run([])
        if args.catalog is not None and args.catalog_config is not None:
            raise LumaModsError("--catalog and --catalog-config are mutually exclusive")
        config_path = args.catalog_config
        if config_path is None and args.catalog is None and SYSTEM_CLIENT_CONFIG.is_file():
            config_path = SYSTEM_CLIENT_CONFIG
        if config_path is not None:
            trusted_catalog = CatalogClientConfig.from_file(config_path).open(
                user_catalog_cache()
            )
            catalog = trusted_catalog.refresh()
            root = trusted_catalog.snapshot.index_path.parent
            policy = trusted_catalog.policy
        else:
            trusted_catalog = None
            root, catalog = load_catalog(args.catalog)
            policy = TrustPolicy.from_file(args.trust_policy) if args.trust_policy else None
    except (LumaModsError, OSError) as error:
        if Path('/.flatpak-info').is_file():
            return ModsApplication(None,None,None,startup_error=str(error)).run([])
        print(f"luma-mods: {error}", file=os.sys.stderr)
        return 2
    return ModsApplication(root, catalog, policy, trusted_catalog).run([])


if __name__ == "__main__":
    raise SystemExit(main())
