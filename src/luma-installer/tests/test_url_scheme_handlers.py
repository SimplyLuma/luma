"""The browser prompt that opens a link must name the application.

A browser asks "Open in ..." by looking up who holds the link's scheme and
reading Name from that desktop entry; when the scheme is held by nothing, or by
a generic helper rather than the application's own launcher, the prompt shows a
command instead of a name. These tests hold that contract for every format Luma
installs.
"""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_installer.desktop import (SYSTEM_SCHEMES, applications_root, capsule_declared_schemes,
                                    desktop_mime_types, scheme_of, write_launcher)

# Anything that opens a link on behalf of whatever holds the scheme, rather than
# being an application a person would recognise by name in a prompt.
GENERIC_HANDLERS = {"xdg-open", "xdg-open.desktop", "gio-launch-desktop.desktop",
                    "org.projectluma.ApplicationInstaller.desktop"}

FAKE_TOOL = """#!/bin/sh
state="$LUMA_TEST_MIME_STATE"
case "$1 $2" in
  "query default")
    python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" "$state" "$3"
    ;;
  *)
    if [ "$1" = "default" ]; then
      shift
      entry="$1"; shift
      for mime in "$@"; do
        python3 -c "import json,sys;s=json.load(open(sys.argv[1]));s[sys.argv[3]]=sys.argv[2];json.dump(s,open(sys.argv[1],'w'))" "$state" "$entry" "$mime"
      done
    fi
    ;;
esac
exit 0
"""


class SchemeHandlerEnvironment:
    """A session with a fake handler database that xdg-mime reads and writes."""

    def __init__(self, root: str):
        self.root = Path(root)
        self.data_home = self.root / "data"
        self.state = self.root / "handlers.json"
        self.state.write_text("{}", encoding="utf-8")
        tools = self.root / "bin"
        tools.mkdir(parents=True, exist_ok=True)
        for name in ("xdg-mime", "update-desktop-database", "gtk-update-icon-cache"):
            tool = tools / name
            tool.write_text(FAKE_TOOL if name == "xdg-mime" else "#!/bin/sh\nexit 0\n", encoding="utf-8")
            tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        self.environment = {
            "XDG_DATA_HOME": str(self.data_home),
            "XDG_DATA_DIRS": str(self.root / "share"),
            "LUMA_TEST_MIME_STATE": str(self.state),
            "PATH": f"{tools}:{os.environ.get('PATH', '')}",
        }

    def handlers(self) -> dict[str, str]:
        return json.loads(self.state.read_text(encoding="utf-8"))

    def hold(self, mime: str, desktop_id: str) -> None:
        state = self.handlers()
        state[mime] = desktop_id
        self.state.write_text(json.dumps(state), encoding="utf-8")

    def install_foreign_entry(self, desktop_id: str, name: str) -> None:
        """A launcher belonging to something other than the application at hand."""
        directory = self.root / "share" / "applications"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / desktop_id).write_text(f"[Desktop Entry]\nType=Application\nName={name}\n", encoding="utf-8")


class SchemeRegistrationTests(unittest.TestCase):
    def test_declared_scheme_resolves_to_the_application_and_not_a_generic_helper(self):
        """The contract: a declared scheme must name the application itself."""
        applications = {
            "rpm-chatgpt-25ec6b75b803": ("ChatGPT", ["x-scheme-handler/codex"]),
            "deb-claude-desktop-797594ce81c1": ("Claude", ["x-scheme-handler/claude"]),
            "appimage-obsidian-1f2e3d4c5b6a": ("Obsidian", ["x-scheme-handler/obsidian"]),
        }
        with tempfile.TemporaryDirectory() as root:
            session = SchemeHandlerEnvironment(root)
            with patch.dict(os.environ, session.environment):
                for application_id, (name, mime_types) in applications.items():
                    write_launcher(application_id, name, f"{name} desktop", "icon", mime_types=mime_types)
                for application_id, (name, mime_types) in applications.items():
                    expected = f"org.projectluma.Installed.{application_id}.desktop"
                    for mime in mime_types:
                        holder = session.handlers().get(mime, "")
                        self.assertNotIn(holder, GENERIC_HANDLERS,
                                         f"{mime} resolves to the generic handler {holder!r}")
                        self.assertEqual(holder, expected, f"{mime} does not resolve to {name}'s own launcher")
                        entry = (applications_root() / holder).read_text(encoding="utf-8")
                        self.assertIn(f"\nName={name}\n", entry)

    def test_unregistered_scheme_leaves_xdg_open_to_answer(self):
        with tempfile.TemporaryDirectory() as root:
            session = SchemeHandlerEnvironment(root)
            with patch.dict(os.environ, session.environment):
                write_launcher("rpm-chatgpt-fixture", "ChatGPT", "ChatGPT", "icon",
                               mime_types=["x-scheme-handler/codex"])
            self.assertNotIn("x-scheme-handler/hypothetical", session.handlers())

    def test_a_declared_link_makes_the_launcher_take_a_url(self):
        with tempfile.TemporaryDirectory() as root:
            session = SchemeHandlerEnvironment(root)
            with patch.dict(os.environ, session.environment):
                path = write_launcher("rpm-figma-fixture", "Figma", "Design", "icon",
                                      mime_types=["x-scheme-handler/figma"])
                contents = path.read_text(encoding="utf-8")
            self.assertIn("Exec=/usr/bin/luma-capsule-launch rpm-figma-fixture %U\n", contents)
            self.assertIn("MimeType=x-scheme-handler/figma;\n", contents)

    def test_a_launcher_without_links_is_left_alone(self):
        with tempfile.TemporaryDirectory() as root:
            session = SchemeHandlerEnvironment(root)
            with patch.dict(os.environ, session.environment):
                path = write_launcher("deb-steam-fixture", "Steam", "Games", "icon")
                contents = path.read_text(encoding="utf-8")
            self.assertNotIn("MimeType=", contents)
            self.assertNotIn("%U", contents)
            self.assertEqual(session.handlers(), {})

    def test_the_browser_and_mail_schemes_are_never_taken(self):
        with tempfile.TemporaryDirectory() as root:
            session = SchemeHandlerEnvironment(root)
            with patch.dict(os.environ, session.environment):
                path = write_launcher("rpm-chatgpt-fixture", "ChatGPT", "ChatGPT", "icon", mime_types=[
                    "x-scheme-handler/codex", "x-scheme-handler/http",
                    "x-scheme-handler/https", "x-scheme-handler/mailto", "text/csv",
                ])
            held = session.handlers()
            self.assertEqual(held.get("x-scheme-handler/codex"),
                             "org.projectluma.Installed.rpm-chatgpt-fixture.desktop")
            for mime in ("x-scheme-handler/http", "x-scheme-handler/https", "x-scheme-handler/mailto"):
                self.assertNotIn(mime, held, f"{mime} must stay the session's own choice")
            # The application still offers to open them; it just is not made the default.
            self.assertIn("x-scheme-handler/https", path.read_text(encoding="utf-8"))
            self.assertTrue({"http", "https", "mailto"} <= SYSTEM_SCHEMES)

    def test_another_application_keeps_the_scheme_it_holds(self):
        with tempfile.TemporaryDirectory() as root:
            session = SchemeHandlerEnvironment(root)
            session.install_foreign_entry("com.example.Notes.desktop", "Notes")
            session.hold("x-scheme-handler/notes", "com.example.Notes.desktop")
            with patch.dict(os.environ, session.environment):
                write_launcher("deb-other-fixture", "Other", "Other", "icon",
                               mime_types=["x-scheme-handler/notes"])
            self.assertEqual(session.handlers()["x-scheme-handler/notes"], "com.example.Notes.desktop")

    def test_a_scheme_held_by_a_launcher_that_is_gone_is_taken_over(self):
        with tempfile.TemporaryDirectory() as root:
            session = SchemeHandlerEnvironment(root)
            session.hold("x-scheme-handler/codex", "chatgpt.desktop")  # never installed on the host
            with patch.dict(os.environ, session.environment):
                write_launcher("rpm-chatgpt-fixture", "ChatGPT", "ChatGPT", "icon",
                               mime_types=["x-scheme-handler/codex"])
            self.assertEqual(session.handlers()["x-scheme-handler/codex"],
                             "org.projectluma.Installed.rpm-chatgpt-fixture.desktop")


class DeclarationTests(unittest.TestCase):
    def test_schemes_the_application_registers_for_itself_are_read_back(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root) / "capsule-home"
            (home / ".config").mkdir(parents=True)
            (home / ".config/mimeapps.list").write_text(
                "\n[Default Applications]\nx-scheme-handler/codex=chatgpt.desktop\n"
                "\n[Added Associations]\nx-scheme-handler/chatgpt=chatgpt.desktop;\n"
                "text/csv=chatgpt.desktop;\n", encoding="utf-8")
            self.assertEqual(capsule_declared_schemes(home),
                             ["x-scheme-handler/codex", "x-scheme-handler/chatgpt"])

    def test_no_private_registration_declares_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(capsule_declared_schemes(Path(root)), [])

    def test_declared_types_are_read_from_the_packaged_launcher(self):
        contents = ("[Desktop Entry]\nName=ChatGPT\nExec=chatgpt %U\n"
                    "MimeType=x-scheme-handler/codex;text/csv;not a mime type;\n")
        self.assertEqual(desktop_mime_types(contents), ["x-scheme-handler/codex", "text/csv"])

    def test_scheme_of_names_only_link_handlers(self):
        self.assertEqual(scheme_of("x-scheme-handler/codex"), "codex")
        self.assertEqual(scheme_of("text/csv"), "")


if __name__ == "__main__":
    unittest.main()
