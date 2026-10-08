# SPDX-License-Identifier: MPL-2.0
"""The rules about overriding a publisher's own command line.

Luma starts some Chromium and Electron applications with switches their
publisher did not choose, because the override is plainly better for the
person -- native Wayland instead of XWayland, video decoded by the chip built
for it. Those are the rules that make that defensible rather than rude, so
they live here, where %check gates shipping on them.
"""
import unittest

from luma_installer import capsule_runtime


class OverridingAPublishersCommandLine(unittest.TestCase):

    def test_an_application_that_breaks_recovers_without_the_person_knowing_why(self):
        import os, tempfile
        from luma_installer import launcher, launch_guard
        home = tempfile.mkdtemp()
        previous = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = home
        attempts: list[list[str]] = []
        reported: list[str] = []
        backoffs: list[str] = []

        def fake_run(arguments, **_kwargs):
            attempts.append(list(arguments))
            # Fails while Luma's switches are present; starts without them.
            ours = [a for a in arguments if a in capsule_runtime.CHROMIUM_FLAGS]
            return (0, 30.0, []) if not ours else (1, 0.2, ["boom"])

        originals = (launch_guard.run, launch_guard.classify, launch_guard.report,
                     launch_guard.journal_backoff, launch_guard.should_capture)
        try:
            launch_guard.run = fake_run
            launch_guard.classify = lambda status, seconds, stderr: (
                None if status == 0 else launch_guard.Failure("crash", status, seconds))
            launch_guard.report = lambda name, *a, **k: reported.append(name)
            launch_guard.journal_backoff = lambda name, i, f, fail: backoffs.append(i)
            launch_guard.should_capture = lambda: False

            record = {"chromium": True, "application_id": "rpm-example-9", "name": "Example"}
            arguments = ["/usr/bin/thing", *capsule_runtime.CHROMIUM_FLAGS]
            status = launcher._guarded(record, arguments)

            # It was tried twice: ours, then the publisher's own.
            self.assertEqual(len(attempts), 2)
            self.assertTrue([a for a in attempts[0] if a in capsule_runtime.CHROMIUM_FLAGS])
            self.assertFalse([a for a in attempts[1] if a in capsule_runtime.CHROMIUM_FLAGS])
            # It started, so the person is told nothing at all.
            self.assertEqual(status, 0)
            self.assertEqual(reported, [])
            # But it is recorded, so the decision is not invisible to everyone.
            self.assertEqual(backoffs, ["rpm-example-9"])
            # And it does not happen twice.
            self.assertIn("all", capsule_runtime.flag_exceptions("rpm-example-9"))
        finally:
            (launch_guard.run, launch_guard.classify, launch_guard.report,
             launch_guard.journal_backoff, launch_guard.should_capture) = originals
            if previous is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = previous

    def test_a_real_failure_is_still_reported_rather_than_blamed_on_our_flags(self):
        import os, tempfile
        from luma_installer import launcher, launch_guard
        home = tempfile.mkdtemp()
        previous = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = home
        reported: list[str] = []
        originals = (launch_guard.run, launch_guard.classify, launch_guard.report,
                     launch_guard.journal_backoff, launch_guard.should_capture)
        try:
            launch_guard.run = lambda arguments, **_k: (1, 0.2, ["boom"])
            launch_guard.classify = lambda status, seconds, stderr: (
                None if status == 0 else launch_guard.Failure("crash", status, seconds))
            launch_guard.report = lambda name, *a, **k: reported.append(name)
            launch_guard.journal_backoff = lambda *a, **k: None
            launch_guard.should_capture = lambda: False
            launcher._guarded({"chromium": True, "application_id": "rpm-broken-3",
                               "name": "Broken"},
                              ["/usr/bin/thing", *capsule_runtime.CHROMIUM_FLAGS])
            self.assertEqual(reported, ["Broken"])
        finally:
            (launch_guard.run, launch_guard.classify, launch_guard.report,
             launch_guard.journal_backoff, launch_guard.should_capture) = originals
            if previous is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = previous

    def test_what_luma_changes_can_be_read_in_words_not_flags(self):
        import os, tempfile
        home = tempfile.mkdtemp()
        previous = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = home
        try:
            rows = capsule_runtime.flag_overrides("rpm-example-4")
            self.assertEqual({r["group"] for r in rows}, set(capsule_runtime.FLAG_GROUPS))
            for row in rows:
                self.assertTrue(row["active"])
                # Every one says what it is, in a sentence, without a flag in it.
                self.assertTrue(row["title"] and row["explanation"])
                self.assertNotIn("--", row["title"] + row["explanation"])
            capsule_runtime.record_flag_backoff("rpm-example-4", "video-decode")
            rows = {r["group"]: r for r in capsule_runtime.flag_overrides("rpm-example-4")}
            self.assertFalse(rows["video-decode"]["active"])
            self.assertTrue(rows["wayland"]["active"])
            # An inactive setting always says why, even with no journal to read.
            self.assertTrue(rows["video-decode"]["withdrawn_because"])
        finally:
            if previous is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = previous

    def test_an_override_the_person_can_always_undo(self):
        import os, tempfile
        home = tempfile.mkdtemp()
        previous = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = home
        try:
            record = {"chromium": True, "application_id": "rpm-example-1"}
            self.assertTrue(capsule_runtime.payload_arguments(record, wayland=True))

            # One launch, nothing written down.
            os.environ["LUMA_ENERGY_FLAGS"] = "off"
            self.assertEqual(capsule_runtime.payload_arguments(record, wayland=True), [])
            del os.environ["LUMA_ENERGY_FLAGS"]

            # Backing off one group keeps the rest, and keeps them in one switch.
            capsule_runtime.record_flag_backoff("rpm-example-1", "video-decode")
            flags = capsule_runtime.payload_arguments(record, wayland=True)
            self.assertIn("--ozone-platform=wayland", flags)
            self.assertEqual(["--enable-features=WaylandWindowDecorations"],
                             [f for f in flags if f.startswith("--enable-features=")])

            # An application that failed on the way up gets its publisher's own
            # command back, rather than being left broken by Luma's improvement.
            capsule_runtime.record_flag_backoff("rpm-example-1")
            self.assertEqual(capsule_runtime.payload_arguments(record, wayland=True), [])

            # A list Luma cannot parse never stops an application starting.
            with open(os.path.join(home, "luma", "energy-flag-exceptions.txt"), "a") as handle:
                handle.write("\n#comment\n   \nnot-this-app all-of-it\n")
            self.assertTrue(capsule_runtime.payload_arguments(
                {"chromium": True, "application_id": "rpm-other-2"}, wayland=True))
        finally:
            if previous is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = previous

    def test_video_is_decoded_by_the_media_engine_and_not_the_processor(self):
        flags = capsule_runtime.payload_arguments({"chromium": True}, wayland=True)
        features = [f for f in flags if f.startswith("--enable-features=")]
        # Repeated switches keep their last value, so there must be exactly one.
        self.assertEqual(len(features), 1, flags)
        named = features[0].split("=", 1)[1].split(",")
        self.assertIn("WaylandWindowDecorations", named)
        # Chromium's accelerated decode is off by default on Linux; a machine
        # with a media engine otherwise decodes every frame on the processor.
        self.assertIn("AcceleratedVideoDecodeLinuxGL", named)
        self.assertIn("VaapiVideoDecodeLinuxGL", named)
        self.assertEqual(len(named), len(set(named)), named)


if __name__ == "__main__":
    unittest.main()
