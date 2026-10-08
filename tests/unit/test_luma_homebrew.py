# SPDX-License-Identifier: Apache-2.0
"""Homebrew on Luma: the folder's owner, the search path, sudo from a guide."""
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2] / "src/luma-homebrew"


class Homebrew(unittest.TestCase):
    def owner(self, passwd, group):
        with tempfile.TemporaryDirectory() as directory:
            p = pathlib.Path(directory, "passwd"); p.write_text(passwd)
            g = pathlib.Path(directory, "group"); g.write_text(group)
            env = {**os.environ, "LUMA_HOMEBREW_PASSWD": str(p), "LUMA_HOMEBREW_GROUP": str(g)}
            env.pop("PKEXEC_UID", None)
            return subprocess.run([ROOT / "libexec/luma-homebrew-prefix", "--print-owner"], env=env,
                                  capture_output=True, text=True, check=True).stdout.strip()

    def test_the_folder_belongs_to_the_first_administrator(self):
        passwd = ("root:x:0:0::/root:/bin/bash\n"
                  "kid:x:1000:1000::/home/kid:/bin/bash\n"
                  "parent:x:1001:1001::/home/parent:/bin/bash\n"
                  "svc:x:990:990::/:/usr/sbin/nologin\n")
        self.assertEqual(self.owner(passwd, "wheel:x:10:parent\n"), "parent")
        self.assertEqual(self.owner(passwd, "wheel:x:10:\n"), "kid")
        self.assertEqual(self.owner("root:x:0:0::/root:/bin/bash\n", "wheel:x:10:\n"), "")

    def test_homebrew_commands_follow_the_system_commands_once(self):
        script = f". {ROOT / 'data/luma-homebrew.sh'}; unset LUMA_HOMEBREW_PROFILE; . {ROOT / 'data/luma-homebrew.sh'}; " \
                 "printf '%s\\n%s' \"$PATH\" \"$HOMEBREW_NO_ANALYTICS\""
        out = subprocess.run(["sh", "-c", script], env={"PATH": "/usr/bin:/bin"},
                             capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(out[0], "/usr/bin:/bin:/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin")
        self.assertEqual(out[1], "1")

    def installed(self, prefix, *arguments, uid="1000", **environment):
        """Run the shim against an installed Homebrew at `prefix`.

        `uid` is what the shim's `id -u` reports. It is stubbed rather than
        inherited because the suite runs as root in CI's container, and as
        root the shim correctly refuses to go any further, which said nothing
        about whether an installed Homebrew receives the command.
        """
        (pathlib.Path(prefix) / "Homebrew/.git").mkdir(parents=True, exist_ok=True)
        (pathlib.Path(prefix) / "bin").mkdir(exist_ok=True)
        real = pathlib.Path(prefix) / "bin/brew"
        real.write_text("#!/bin/sh\necho \"real $HOMEBREW_NO_ANALYTICS $*\"\n")
        real.chmod(0o755)
        stubs = pathlib.Path(prefix) / "stubs"
        stubs.mkdir(exist_ok=True)
        identity = stubs / "id"
        identity.write_text(
            "#!/bin/sh\n"
            f"case \"$1\" in -u) echo {uid} ;; -un) echo tester ;; *) exec /usr/bin/id \"$@\" ;; esac\n"
        )
        identity.chmod(0o755)
        env = {**os.environ, "LUMA_HOMEBREW_PREFIX": prefix,
               "PATH": f"{stubs}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
        env.pop("HOMEBREW_NO_ANALYTICS", None)
        env.pop("SUDO_USER", None)
        env.update(environment)
        return subprocess.run([ROOT / "bin/brew", *arguments], env=env,
                              capture_output=True, text=True)

    def test_an_installed_homebrew_receives_every_command(self):
        with tempfile.TemporaryDirectory() as prefix:
            out = self.installed(prefix, "install", "hello")
            self.assertEqual(out.stdout.strip(), "real 1 install hello")

    def test_root_is_sent_back_rather_than_running_homebrew_as_root(self):
        """Homebrew refuses to run as root, so the shim says so in Luma's words."""
        with tempfile.TemporaryDirectory() as prefix:
            out = self.installed(prefix, "install", "hello", uid="0")
            self.assertEqual(out.returncode, 1)
            self.assertEqual(out.stdout.strip(), "")
            self.assertIn("without sudo", out.stderr)


if __name__ == "__main__":
    unittest.main()
