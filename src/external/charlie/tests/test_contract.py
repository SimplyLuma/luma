# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


# Native layout/action/geometry contracts are exercised by v71_window_runtime.py,
# which constructs the shared window. This suite retains source/security and
# package identity boundaries that do not depend on a former monolithic layout.
class ContractTests(unittest.TestCase):
    def test_identity_is_one_value_everywhere(self):
        expected = "org.projectluma.Charlie"
        for relative in (
            "luma-app.toml",
            "data/org.projectluma.Charlie.desktop",
            "data/org.projectluma.Charlie.metainfo.xml",
            "data/org.projectluma.Charlie.service",
            "data/org.projectluma.Charlie.search-provider.ini",
            "charlie_luma/__init__.py",
        ):
            self.assertIn(expected, (ROOT / relative).read_text(), relative)
        application = (ROOT / "charlie_luma/application.py").read_text()
        self.assertIn('GLib.set_application_name("Charlie")', application)
        self.assertIn("GLib.set_prgname(APP_ID)", application)

    def test_icon_uses_approved_v71_geometry(self):
        icon = (ROOT / "data/icons/hicolor/scalable/apps/org.projectluma.Charlie.svg").read_text()
        import xml.etree.ElementTree as ET
        tree = ET.fromstring(icon)
        self.assertEqual(tree.get("viewBox"), "82 82 860 860")
        self.assertTrue(tree.findall(".//{http://www.w3.org/2000/svg}path"))

    def test_all_icon_exports_are_generated_from_one_svg(self):
        meson = (ROOT / "meson.build").read_text()
        self.assertIn("rsvg_converter = find_program('rsvg-convert')", meson)
        for size in ("16x16", "24x24", "32x32", "48x48", "64x64", "128x128", "256x256", "512x512"):
            directory = ROOT / "data/icons/hicolor" / size / "apps"
            self.assertFalse((directory / "org.projectluma.Charlie.png").exists())
            target = (directory / "meson.build").read_text()
            self.assertIn("input: icon_svg", target)
            self.assertIn("rsvg_converter", target)
            self.assertIn("install: true", target)

    def test_engine_never_imports_gtk(self):
        for relative in ("model.py", "mime.py", "store.py", "engine.py"):
            source = (ROOT / "charlie_luma" / relative).read_text()
            self.assertNotIn("gi.repository", source, relative)
            self.assertNotRegex(source, r"\bGtk\b", relative)
        # The background agent uses Gio/GLib for the bus, never a toolkit.
        for relative in ("mail_agent.py", "background.py"):
            source = (ROOT / "charlie_luma" / relative).read_text()
            self.assertNotRegex(source, r"import .*\b(Gtk|Gdk|Adw|WebKit)\b", relative)
            self.assertNotIn("from .application", source, relative)
            self.assertNotIn("luma_appkit import", source, relative)

    def test_css_uses_named_luma_colours_only(self):
        css = (ROOT / "data/charlie.css").read_text()
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotIn("rgba(", css)
        self.assertNotIn("@define-color", css)
        self.assertNotRegex(css, r"\b(headerbar|windowcontrols|luma-titlebar|luma-window-body|luma-island)\b")

    def test_html_reader_is_locked_down(self):
        source = (ROOT / "charlie_luma/html_reader.py").read_text()
        self.assertIn("set_enable_javascript(False)", source)
        self.assertIn("set_enable_plugins(False)", source)
        self.assertIn("default-src 'none'", source)
        self.assertIn("decision.ignore()", source)
        self.assertIn('name=\\"color-scheme\\" content=\\"light\\"', source)
        self.assertIn("background:Canvas;color:CanvasText", source)

    def test_no_flutter_tray_or_jvm_runtime(self):
        sources = "\n".join(path.read_text(errors="replace") for path in (ROOT / "charlie_luma").rglob("*.py"))
        self.assertNotIn("Flutter", sources)
        self.assertNotRegex(sources, r"\bJNI\b|system tray|AppIndicator")

    def test_search_activation_opens_no_window(self):
        service = (ROOT / "data/org.projectluma.Charlie.service").read_text()
        self.assertIn("Exec=/usr/bin/org.projectluma.Charlie --gapplication-service", service)
        application = (ROOT / "charlie_luma/application.py").read_text()
        register = application.split("def do_dbus_register", 1)[1].split("def ", 1)[0]
        self.assertIn("self.search_provider.export(connection)", register)
        self.assertEqual(application.count("search_provider.export("), 1)

    def test_desktop_identity_registers_mailto_and_dbus_activation(self):
        desktop = (ROOT / "data/org.projectluma.Charlie.desktop").read_text()
        self.assertIn("x-scheme-handler/mailto", desktop)
        self.assertIn("DBusActivatable=true", desktop)


    def test_search_provider_is_local_bounded_and_registered(self):
        provider = (ROOT / "charlie_luma/search_provider.py").read_text()
        registration = (ROOT / "data/org.projectluma.Charlie.search-provider.ini").read_text()
        self.assertIn("org.gnome.Shell.SearchProvider2", provider)
        self.assertIn("search_message_ids", provider)
        self.assertIn("BusName=org.projectluma.Charlie", registration)
        self.assertIn("DefaultDisabled=false", registration)

    def test_agent_notifications_name_sender_and_subject_with_lock_screen_privacy(self):
        # New-mail notifications moved from the window process to the background
        # agent (0.2.7). They now intentionally show the sender and subject, but
        # only the public "Charlie" / "New mail" text while the screen is locked
        # and details are not allowed there. Bodies are never shown.
        application = (ROOT / "charlie_luma/application.py").read_text()
        agent = (ROOT / "charlie_luma/mail_agent.py").read_text()
        background = (ROOT / "charlie_luma/background.py").read_text()
        self.assertNotIn("send_notification(", application)
        for action in ('("open-message"', '("mark-read"', '("archive-message"'):
            self.assertIn(action, application)
        self.assertIn('"open-message", [GLib.Variant("s", message_id)]', agent)
        self.assertIn('key = f"message-{item.message_id}"', agent)
        self.assertIn('public_summary="Charlie", public_body="New mail"', agent)
        self.assertIn('("mark-read", "Mark Read")', agent)
        self.assertIn('NOTIFICATION_CATEGORY = "email.arrived"', agent)
        self.assertNotIn("body_text", agent)
        self.assertIn("def private(self) -> bool:", background)
        self.assertIn("return self._locked() and not self._details()", background)

    def test_background_agent_follows_the_published_luma_background_contract(self):
        import tomllib

        # luma-background generates agent units itself; a shipped unit or an
        # activation file pointing at one would bypass the person's decision.
        self.assertFalse((ROOT / "data/app-org.projectluma.Charlie-agent.service").exists())
        self.assertFalse((ROOT / "data/org.projectluma.Charlie.Agent.service").exists())
        meson = (ROOT / "meson.build").read_text()
        spec = (ROOT / "packaging/luma-charlie.spec").read_text()
        for text in (meson, spec):
            self.assertNotIn("systemd/user", text)
            self.assertNotIn("_userunitdir", text)
            self.assertNotIn("Charlie.Agent.service", text)
        self.assertIn("install_dir: get_option('datadir') / 'luma/background'", meson)
        self.assertIn("%{_datadir}/luma/background/org.projectluma.Charlie.toml", spec)
        self.assertIn("%{_sysconfdir}/xdg/autostart/org.projectluma.Charlie.Agent.desktop", spec)
        for name in ("mail_agent.py", "background.py"):
            self.assertIn(f"'charlie_luma/{name}'", meson)

        declaration = tomllib.loads((ROOT / "data/background/org.projectluma.Charlie.toml").read_text())
        manifest = tomllib.loads((ROOT / "luma-app.toml").read_text())
        self.assertEqual(declaration["application"], {"id": "org.projectluma.Charlie", "name": "Charlie"})
        self.assertEqual(declaration["background"], manifest["background"])
        background = declaration["background"]
        self.assertEqual(background["agent"], "org.projectluma.Charlie.Agent")
        self.assertEqual(background["category"], "mail")
        self.assertEqual(background["wake"], ["login", "network", "resume", "schedule"])
        for name in background["publishes"]:
            self.assertRegex(name, r"^(?:[a-z][a-z0-9._-]{0,63}|live-extension:[A-Za-z0-9._-]+)$")
        desktop = (ROOT / "data/org.projectluma.Charlie.desktop").read_text()
        exec_line = next(line for line in desktop.splitlines() if line.startswith("Exec="))
        self.assertEqual(background["exec"].split()[0], exec_line[len("Exec="):].split()[0])

        autostart = (ROOT / "data/org.projectluma.Charlie.Agent.desktop").read_text()
        self.assertIn("Exec=org.projectluma.Charlie --agent --autostart\n", autostart)
        self.assertIn("NoDisplay=true\n", autostart)

        contract = (ROOT / "charlie_luma/background.py").read_text()
        self.assertIn('AGENT_PATH = "/org/projectluma/BackgroundAgent1"', contract)
        self.assertIn('AGENT_INTERFACE = "org.projectluma.BackgroundAgent1"', contract)
        self.assertNotIn("/org/projectluma/Background/Agent", contract)
        self.assertNotIn("StartUnit", contract)
        agent = (ROOT / "charlie_luma/mail_agent.py").read_text()
        self.assertIn('self.publish("unread-count", ', agent)
        self.assertIn('self.publish("unread-by-account", ', agent)
        self.assertIn('if autostart and service_installed(connection):', agent)

        application = (ROOT / "charlie_luma/application.py").read_text()
        self.assertNotIn("start_agent", application)
        self.assertIn("request_background(APP_ID, reason=AGENT_REASON, autostart=True", application)
        self.assertNotIn("Gio.DBusCallFlags.NONE if method", application)
        # Behavioral tests in test_host_mail_application verify exact Reload
        # delivery for the independent UI. Counting source spelling rejects
        # additional legitimate account flows without checking their behavior.
        self.assertIn('self.app.call_mail_agent("CheckNow")', (ROOT / "charlie_luma/window.py").read_text())


    def test_attachment_export_keeps_private_file_permissions(self):
        source = (ROOT / "charlie_luma/application.py").read_text()
        self.assertIn("destination.chmod(0o600)", source)
        self.assertIn("Gtk.FileLauncher.new", source)
        window = (ROOT / "charlie_luma/window.py").read_text()
        self.assertIn("def _open_attachment", window)
        self.assertIn("def _save_attachment", window)

    def test_sync_includes_sent_mail_for_complete_conversations(self):
        engine = (ROOT / "charlie_luma/engine.py").read_text()
        store = (ROOT / "charlie_luma/store.py").read_text()
        self.assertIn("def fetch_mail(", engine)
        self.assertIn('self._sent_mailbox(client, account), "sent"', engine)
        self.assertIn('outgoing=folder == "sent"', engine)
        self.assertIn('folder in {"inbox", "sent"}', store)


    def test_visual_fixture_capture_never_takes_a_desktop_screenshot(self):
        source = (ROOT / "charlie_luma/application.py").read_text()
        self.assertIn('os.environ.pop("CHARLIE_TEST_SCREENSHOT", "")', source)
        self.assertIn("Gtk.WidgetPaintable.new(self.window)", source)
        self.assertNotIn("org.gnome.Shell.Screenshot", source)

    def test_icon_is_exact_approved_v71_mail_artwork(self):
        import hashlib
        icon = ROOT / "data/icons/hicolor/scalable/apps/org.projectluma.Charlie.svg"
        self.assertEqual(hashlib.sha256(icon.read_bytes()).hexdigest(),
                         "0bd4e8a2750f599cf686c79e78410d6f8a735b8226d9289312f90b88d797f209")

    def test_account_enrollment_is_real_and_secrets_stay_out_of_sqlite(self):
        application = (ROOT / "charlie_luma/application.py").read_text()
        account_ui = (ROOT / "charlie_luma/account_ui.py").read_text()
        store = (ROOT / "charlie_luma/store.py").read_text()
        self.assertIn("LumaAccountEditorWindow", application)
        self.assertIn("class AccountEditorWindow", account_ui)
        self.assertIn("NavigationRow(", account_ui)
        self.assertIn('self.stack.add_named(self._provider_page(), "providers")', account_ui)
        self.assertIn('label="Show server settings"', account_ui)
        self.assertNotIn("Gtk.DropDown", account_ui)
        self.assertIn("self.app.show_accounts()", (ROOT / "charlie_luma/window.py").read_text())
        self.assertIn("ImapSmtpTransport(self.secrets.lookup, self.oauth_access_token)", application)
        self.assertIn("self.secrets.store(account.id, \"password\", password)", application)
        self.assertIn("CREATE TABLE server_configs", store)
        config_table = store[store.index("CREATE TABLE server_configs"):]
        self.assertNotIn("password", config_table.split(");", 1)[0])
        self.assertNotIn("Account setup is not enabled", application)

    def test_google_pauses_while_microsoft_keeps_browser_oauth_and_xoauth2(self):
        application = (ROOT / "charlie_luma/application.py").read_text()
        account_ui = (ROOT / "charlie_luma/account_ui.py").read_text()
        oauth = (ROOT / "charlie_luma/oauth.py").read_text()
        engine = (ROOT / "charlie_luma/engine.py").read_text()
        self.assertIn("Google sign-in unavailable", account_ui)
        self.assertIn("Continue with Microsoft", account_ui)
        self.assertIn("begin_microsoft_sign_in", application)
        self.assertIn('self.secrets.store(account.id, "oauth-token"', application)
        self.assertIn("code_challenge_method", oauth)
        self.assertIn("https://mail.google.com/", oauth)
        self.assertIn("IMAP.AccessAsUser.All", oauth)
        self.assertIn("SMTP.Send", oauth)
        self.assertIn('client.authenticate("XOAUTH2"', engine)
        self.assertIn('"AUTH", "XOAUTH2 "', engine)
        self.assertIn("force_refresh=True", engine)
        self.assertIn("failure_requires_sign_in", application)
