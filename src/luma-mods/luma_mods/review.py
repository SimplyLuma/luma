"""Adaptive, read-only graphical review for one resolved Luma Mod plan."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from luma_appkit import AppWindow, CommandRegistry, Island, Toolbar

from .errors import LumaModsError
from .catalog_runtime import TrustedPreferenceTransaction
from .catalog_update import TrustedCatalogPlan
from .manifest import inspect_manifest
from .lifecycle import Authorization
from .model import Plan
from .profile import PreferenceProfile
from .resolver import Catalog, HostContext, assess_mod, resolve_mod
from .runtime import UserRuntime, authorization_for_plan
from .trust import TrustPolicy, VerificationResult, verify_inspection

IMPACT_LABELS = {
    "application": "App",
    "appearance": "Appearance",
    "behavior": "Behavior",
    "capability": "Capability",
    "developer": "Developer",
    "compatibility": "Compatibility",
    "experience": "Experience",
    "hardware": "Hardware",
    "core-system": "Core System",
}
ACTIVATION_LABELS = {
    "live": "Works immediately",
    "service-restart": "Restarts a background service",
    "session-restart": "Requires signing out",
    "reboot": "Requires a restart",
    "recovery-reboot": "Requires a protected restart",
}
EFFECT_LABELS = {
    "settings": "Settings",
    "files": "System files",
    "packages": "System packages",
    "services": "Background services",
    "dbus_names": "System interfaces",
    "portals": "App permissions interfaces",
    "devices": "Hardware access",
    "configuration_domains": "System configuration",
    "kernel_modules": "Kernel drivers",
    "firmware": "Device firmware",
    "boot_arguments": "Startup configuration",
    "user_data": "Data locations",
    "initramfs": "Early startup image",
    "secure_boot": "Secure Boot",
}


def _label(text: str, css_class: str | None = None, *, wrap: bool = True) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, wrap=wrap, selectable=False)
    if css_class:
        label.add_css_class(css_class)
    return label


def _row(title: str, subtitle: str = "", icon: str | None = None) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    if icon:
        row.add_prefix(Gtk.Image(icon_name=icon))
    return row


class ReviewWindow(AppWindow):
    def __init__(
        self,
        application: Adw.Application,
        plan: Plan,
        verifications: dict[str, VerificationResult],
        runtime: UserRuntime | None = None,
        profile: PreferenceProfile | None = None,
        policy: TrustPolicy | None = None,
        changed_callback=None,
        trusted_plan: TrustedCatalogPlan | None = None,
        catalog_transaction: Callable[[], TrustedPreferenceTransaction] | None = None,
        system_stage: Callable[[Callable[[str | None, Exception | None], None]], None] | None = None,
        system_activate: Callable[[str, Callable[[str | None, Exception | None], None]], None] | None = None,
    ) -> None:
        target = next(
            item for item in plan.mods if item.inspection.mod.identity.id == plan.target_id
        )
        identity = target.inspection.mod.identity
        super().__init__(application=application, app_id="org.projectluma.ModReview", title=identity.name, icon_name="org.projectluma.ModReview", commands=CommandRegistry(()), default_width=700, default_height=820, minimum_width=340, minimum_height=480)
        self._plan = plan
        self._verifications = verifications
        self._runtime = runtime
        self._profile = profile
        self._policy = policy
        self._changed_callback = changed_callback
        self._trusted_plan = trusted_plan
        self._catalog_transaction = catalog_transaction
        self._system_stage = system_stage
        self._system_activate = system_activate
        self._system_candidate: str | None = None
        self._system_busy = False
        self._target_id = identity.id
        self._target_version = identity.version
        if os.environ.get("LUMA_PRESENTATION_MODE", "").strip().lower() == "fullscreen-mobile":
            self.fullscreen()

        toolbar = Adw.ToolbarView()
        toolbar.set_vexpand(True)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_vexpand(True)
        clamp = Adw.Clamp(maximum_size=720, tightening_threshold=560)
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=24,
            margin_bottom=24,
            margin_start=18,
            margin_end=18,
        )
        clamp.set_child(content)
        scroll.set_child(clamp)
        toolbar.set_content(scroll)

        content.append(self._hero(plan, target, verifications[plan.target_id]))
        content.append(self._overview(plan))
        if plan.dependencies:
            content.append(self._dependencies(plan, verifications))
        if plan.provider_transitions:
            content.append(self._provider_transitions(plan))
        content.append(self._changes(plan))
        content.append(self._trust(target, verifications[plan.target_id]))
        content.append(self._technical(plan))
        toolbar.add_bottom_bar(self._footer())
        self._toasts = Adw.ToastOverlay(child=toolbar)
        self._toasts.set_vexpand(True)
        island = Island()
        island.append(self._toasts)
        self.set_body(island)

    def _hero(
        self, plan: Plan, target, verification: VerificationResult
    ) -> Gtk.Widget:
        identity = target.inspection.mod.identity
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        icon = Gtk.Image(icon_name="application-x-addon-symbolic", pixel_size=56)
        icon.set_halign(Gtk.Align.START)
        box.append(icon)
        box.append(_label(identity.name, "title-1"))
        box.append(_label(identity.summary, "body"))
        box.append(
            _label(
                f"By {identity.publisher.name} · {verification.label}",
                "dim-label",
            )
        )
        return box

    def _overview(self, plan: Plan) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title="At a glance")
        group.add(
            _row(
                IMPACT_LABELS.get(plan.impact, plan.impact.title()),
                "The highest independently calculated impact in this plan.",
                "dialog-information-symbolic",
            )
        )
        group.add(
            _row(
                ACTIVATION_LABELS.get(plan.activation, plan.activation),
                "Luma calculated this from the complete set of declared effects.",
                "system-reboot-symbolic" if "reboot" in plan.activation else "object-select-symbolic",
            )
        )
        group.add(
            _row(
                f"{len(plan.mods)} Mod{'s' if len(plan.mods) != 1 else ''} included",
                "Required Mods are reviewed and resolved as one transaction.",
                "view-list-symbolic",
            )
        )
        return group

    def _dependencies(
        self, plan: Plan, verifications: dict[str, VerificationResult]
    ) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title="Required Mods",
            description="These requirements are declared by the maintainer and checked by Luma.",
        )
        for dependency in plan.dependencies:
            state = "Already installed" if dependency.already_installed else "Will be included"
            verification = verifications.get(dependency.dependency_id)
            trust = verification.label if verification else "Installed state only"
            row = _row(
                dependency.dependency_name,
                f"{dependency.reason}\n{dependency.dependency_publisher} · {trust} · {state}",
                "application-x-addon-symbolic",
            )
            version = Gtk.Label(
                label=(
                    dependency.selected_version
                    if dependency.version == f"={dependency.selected_version}"
                    else f"{dependency.selected_version} · requires {dependency.version}"
                )
            )
            version.add_css_class("dim-label")
            version.set_valign(Gtk.Align.CENTER)
            row.add_suffix(version)
            group.add(row)
        return group

    def _changes(self, plan: Plan) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title="What this changes",
            description="This list comes from the resolved effects, not promotional text.",
        )
        any_effect = False
        for planned in plan.mods:
            populated = planned.inspection.mod.effects.populated()
            for field, value in populated.items():
                any_effect = True
                if isinstance(value, tuple):
                    detail = "\n".join(value)
                elif isinstance(value, bool):
                    detail = "Changed" if value else "Unchanged"
                else:
                    detail = str(value)
                group.add(_row(EFFECT_LABELS.get(field, field.replace("_", " ").title()), detail))
        if not any_effect:
            group.add(_row("No effects declared", "This Mod cannot be installed until its effects are complete."))
        return group

    def _provider_transitions(self, plan: Plan) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title="Desktop providers",
            description=(
                "These active services change together only after the required session transition. "
                "The current desktop remains available for recovery."
            ),
        )
        for transition in plan.provider_transitions:
            group.add(
                _row(
                    transition.capability_id,
                    (
                        f"{transition.previous_provider} {transition.previous_version} → "
                        f"{transition.next_provider} {transition.next_version}\n"
                        f"{transition.reason}"
                    ),
                    "object-select-symbolic",
                )
            )
        return group

    def _trust(self, target, verification: VerificationResult) -> Gtk.Widget:
        identity = target.inspection.mod.identity
        group = Adw.PreferencesGroup(
            title="Publisher and verification",
            description="Catalog presence does not imply verification.",
        )
        group.add(_row(identity.publisher.name, identity.publisher.id, "avatar-default-symbolic"))
        group.add(
            _row(
                verification.label,
                verification.reason,
                "emblem-ok-symbolic" if verification.verified else "dialog-warning-symbolic",
            )
        )
        if verification.signer_identity:
            group.add(
                _row(
                    "Signing identity",
                    f"{verification.signer_identity}\nIssuer: {verification.issuer}",
                    "security-high-symbolic",
                )
            )
        group.add(_row("License", ", ".join(identity.licenses), "text-x-generic-symbolic"))
        return group

    def _technical(self, plan: Plan) -> Gtk.Widget:
        target = next(
            item for item in plan.mods if item.inspection.mod.identity.id == plan.target_id
        )
        inspection = target.inspection
        expander = Gtk.Expander(label="Technical details")
        expander.add_css_class("card")
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        for title, value in (
            ("Mod ID", inspection.mod.identity.id),
            ("Version", inspection.mod.identity.version),
            ("Manifest", f"sha256:{inspection.canonical_sha256}"),
            ("Signing subject", f"sha256:{inspection.signing_sha256}"),
            ("Composition", f"sha256:{plan.composition_sha256}"),
        ):
            box.append(_label(title, "heading"))
            value_label = _label(value, "dim-label")
            value_label.set_selectable(True)
            box.append(value_label)
        expander.set_child(box)
        return expander

    def _footer(self) -> Gtk.Widget:
        self._footer_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=18,
            margin_end=18,
        )
        self._footer_box.add_css_class("toolbar")
        self._populate_footer()
        return self._footer_box

    def _populate_footer(self) -> None:
        child = self._footer_box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._footer_box.remove(child)
            child = following

        if self._system_stage is not None:
            note_text = (
                "Prepared as a recoverable system deployment; restart only after staging succeeds."
            )
        elif self._runtime is None or self._profile is None:
            note_text = "Review only — this Mod has no supported install profile."
        else:
            note_text = "Changes are atomic and can be disabled or removed."
        note = _label(note_text, "dim-label")
        note.set_hexpand(True)
        note.set_valign(Gtk.Align.CENTER)
        self._footer_box.append(note)

        if self._runtime is not None and self._profile is not None:
            state = self._runtime.store.read()
            installed = state["installed"].get(self._target_id)
            if installed is None:
                self._footer_box.append(
                    self._action_button("Install", self._request_install, suggested=True)
                )
            else:
                if installed["version"] != self._target_version:
                    self._footer_box.append(
                        self._action_button("Update", self._request_update, suggested=True)
                    )
                self._footer_box.append(
                    self._action_button(
                        "Disable" if installed["enabled"] else "Enable",
                        self._toggle_enabled,
                        suggested=not installed["enabled"],
                    )
                )
                remove = self._action_button("Remove", self._remove)
                remove.add_css_class("destructive-action")
                self._footer_box.append(remove)
        elif self._system_stage is not None:
            if self._system_candidate is None:
                prepare = self._action_button(
                    "Preparing…" if self._system_busy else "Prepare",
                    self._prepare_system,
                    suggested=True,
                )
                prepare.set_sensitive(not self._system_busy)
                self._footer_box.append(prepare)
            else:
                restart = self._action_button(
                    "Restart & Apply", self._request_system_activation, suggested=True
                )
                self._footer_box.append(restart)

        done = self._action_button("Done", lambda _button: self.close())
        self._footer_box.append(done)

    @staticmethod
    def _action_button(label: str, callback, *, suggested: bool = False) -> Gtk.Button:
        button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        if suggested:
            button.add_css_class("suggested-action")
        button.connect("clicked", callback)
        return button

    def _needs_unverified_confirmation(self) -> bool:
        return any(not result.verified for result in self._verifications.values())

    def _confirm_unverified(self, callback) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Local / Unverified Mod",
            body=(
                "Luma could not verify every publisher in this plan. Review the "
                "declared effects and source details before continuing."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("continue", "Install Anyway")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance(
            "continue", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.connect(
            "response",
            lambda _dialog, response: callback() if response == "continue" else None,
        )
        dialog.present()

    def _authorization(self, confirmed: bool) -> Authorization:
        if self._runtime is not None and hasattr(self._runtime, 'authority'):
            return self._runtime.authority(self._plan,confirmed)
        if self._trusted_plan is not None:
            return self._trusted_plan.authorization(
                confirmed_unverified=confirmed
            )
        return authorization_for_plan(
            self._plan,
            self._policy,
            confirmed_unverified=confirmed,
        )

    def _request_install(self, _button) -> None:
        if self._needs_unverified_confirmation():
            self._confirm_unverified(lambda: self._install(True))
        else:
            self._install(False)

    def _request_update(self, _button) -> None:
        if self._needs_unverified_confirmation():
            self._confirm_unverified(lambda: self._update(True))
        else:
            self._update(False)

    def _prepare_system(self, _button) -> None:
        if self._system_stage is None or self._system_busy:
            return
        self._system_busy = True
        self._populate_footer()

        def complete(candidate: str | None, error: Exception | None) -> None:
            self._system_busy = False
            if error is not None or not candidate:
                self._toasts.add_toast(
                    Adw.Toast(title=str(error or "System Mod preparation failed"), timeout=6)
                )
            else:
                self._system_candidate = candidate
                self._toasts.add_toast(
                    Adw.Toast(title="System Mod is ready to apply", timeout=4)
                )
            self._populate_footer()

        self._system_stage(complete)

    def _request_system_activation(self, _button) -> None:
        if self._system_candidate is None or self._system_activate is None:
            return
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Restart and apply this Mod?",
            body=(
                "Luma retained the known-good system. If the new deployment does not "
                "pass its health checks, Luma will return to the previous one."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restart", "Restart & Apply")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _dialog, response: self._activate_system()
            if response == "restart" else None,
        )
        dialog.present()

    def _activate_system(self) -> None:
        assert self._system_candidate is not None and self._system_activate is not None
        candidate = self._system_candidate

        def complete(_response: str | None, error: Exception | None) -> None:
            if error is not None:
                self._toasts.add_toast(Adw.Toast(title=str(error), timeout=6))

        self._system_activate(candidate, complete)

    def _install(self, confirmed: bool) -> None:
        assert self._runtime is not None and self._profile is not None
        if self._catalog_transaction is not None:
            def install_from_catalog(generation: int):
                transaction = self._catalog_transaction()
                return self._runtime.lifecycle.install(
                    transaction.trusted.plan,
                    transaction.profile,
                    transaction.trusted.authorization(
                        confirmed_unverified=confirmed
                    ),
                    expected_generation=generation,
                )

            self._run_transaction(install_from_catalog, "Installed")
            return
        self._run_transaction(
            lambda generation: self._runtime.lifecycle.install(
                self._plan,
                self._profile,
                self._authorization(confirmed),
                expected_generation=generation,
            ),
            "Installed",
        )

    def _update(self, confirmed: bool) -> None:
        assert self._runtime is not None and self._profile is not None
        if self._catalog_transaction is not None:
            def update_from_catalog(generation: int):
                transaction = self._catalog_transaction()
                return self._runtime.lifecycle.update(
                    transaction.trusted.plan,
                    transaction.profile,
                    transaction.trusted.authorization(
                        confirmed_unverified=confirmed
                    ),
                    expected_generation=generation,
                )

            self._run_transaction(update_from_catalog, "Updated")
            return
        self._run_transaction(
            lambda generation: self._runtime.lifecycle.update(
                self._plan,
                self._profile,
                self._authorization(confirmed),
                expected_generation=generation,
            ),
            "Updated",
        )

    def _toggle_enabled(self, _button) -> None:
        assert self._runtime is not None
        enabled = self._runtime.store.read()["installed"][self._target_id]["enabled"]
        self._run_transaction(
            lambda generation: self._runtime.lifecycle.set_enabled(
                self._target_id, not enabled, expected_generation=generation
            ),
            "Enabled" if not enabled else "Disabled",
        )

    def _remove(self, _button) -> None:
        assert self._runtime is not None
        self._run_transaction(
            lambda generation: self._runtime.lifecycle.remove(
                self._target_id, expected_generation=generation
            ),
            "Removed",
        )

    def _run_transaction(self, operation, success: str) -> None:
        assert self._runtime is not None
        try:
            operation(self._runtime.store.read()["generation"])
        except (LumaModsError, OSError) as error:
            self._toasts.add_toast(Adw.Toast(title=str(error), timeout=5))
            return
        self._toasts.add_toast(Adw.Toast(title=success, timeout=3))
        self._populate_footer()
        if self._changed_callback:
            self._changed_callback()


class ReviewApplication(Adw.Application):
    def __init__(
        self,
        plan: Plan,
        verifications: dict[str, VerificationResult],
        runtime: UserRuntime | None = None,
        profile: PreferenceProfile | None = None,
        policy: TrustPolicy | None = None,
    ) -> None:
        super().__init__(
            application_id="org.projectluma.ModReview",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.plan = plan
        self.verifications = verifications
        self.runtime = runtime
        self.profile = profile
        self.policy = policy

    def do_activate(self) -> None:
        window = self.props.active_window or ReviewWindow(
            self,
            self.plan,
            self.verifications,
            self.runtime,
            self.profile,
            self.policy,
        )
        window.present()


def _arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review a Luma Mod without installing it.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--host", type=Path)
    parser.add_argument("--trust-policy", type=Path)
    parser.add_argument(
        "--profile",
        type=Path,
        help="enable lifecycle actions for this exact bounded preference profile",
    )
    return parser.parse_args(arguments)


def _host(path: Path) -> HostContext:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise LumaModsError("host context must be a JSON object")
    return HostContext.from_dict(value)


def main(arguments: list[str] | None = None) -> int:
    args = _arguments(arguments)
    try:
        inspection = inspect_manifest(args.manifest)
        policy = TrustPolicy.from_file(args.trust_policy) if args.trust_policy else None
        if bool(args.catalog) != bool(args.host):
            raise LumaModsError("--catalog and --host must be supplied together")
        if args.catalog:
            catalog = Catalog.from_directory(args.catalog)
            plan = resolve_mod(
                inspection.mod.identity.id,
                catalog,
                _host(args.host),
            )
        else:
            assessment = assess_mod(inspection)
            from .model import PlannedMod

            plan = Plan(
                target_id=inspection.mod.identity.id,
                mods=(PlannedMod(inspection, assessment),),
                dependencies=(),
                impact=assessment.impact,
                activation=assessment.activation,
                composition_sha256=inspection.canonical_sha256,
                provider_transitions=(),
            )
        verifications = {
            planned.inspection.mod.identity.id: verify_inspection(
                planned.inspection, policy
            )
            for planned in plan.mods
        }
        if args.catalog:
            for dependency in plan.dependencies:
                verifications[dependency.dependency_id] = verify_inspection(
                    catalog.require(dependency.dependency_id), policy
                )
        profile = PreferenceProfile.from_file(args.profile) if args.profile else None
        runtime = UserRuntime.current() if profile else None
    except (LumaModsError, OSError, json.JSONDecodeError) as error:
        print(f"luma-mod-review: {error}", file=os.sys.stderr)
        return 2
    return ReviewApplication(
        plan, verifications, runtime=runtime, profile=profile, policy=policy
    ).run([])


if __name__ == "__main__":
    raise SystemExit(main())
