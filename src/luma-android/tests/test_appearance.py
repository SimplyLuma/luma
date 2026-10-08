import threading
import unittest
from unittest import mock

from luma_android import appearance

try:
    from luma_android.service import Service
except ImportError:
    Service = None


class AppearanceTests(unittest.TestCase):
    def test_host_preference_and_missing_schema(self):
        with mock.patch.object(appearance, "settings", return_value=None):
            self.assertEqual(appearance.color_scheme(), "light")
        settings = mock.Mock()
        with mock.patch.object(appearance, "settings", return_value=settings):
            for preference, expected in (("prefer-dark", "dark"), ("default", "light")):
                settings.get_string.return_value = preference
                self.assertEqual(appearance.color_scheme(), expected)

    def test_surface_treatment_uses_shell_authority_with_color_fallback(self):
        treatment = mock.Mock()
        treatment.get_boolean.return_value = False
        treatment.get_user_value.return_value = object()
        with mock.patch.object(appearance, "treatment_settings", return_value=treatment), \
             mock.patch.object(appearance, "accessibility_settings", return_value=None), \
             mock.patch.object(appearance, "color_scheme", return_value="dark"):
            for requested in ("light", "dark", "frost", "glass"):
                treatment.get_string.return_value = requested
                self.assertEqual(appearance.surface_treatment(), requested)
            treatment.get_string.return_value = "invalid"
            self.assertEqual(appearance.surface_treatment(), "dark")
            treatment.get_user_value.return_value = None
            treatment.get_string.return_value = "light"
            self.assertEqual(appearance.surface_treatment(), "dark")
        with mock.patch.object(appearance, "treatment_settings", return_value=None), \
             mock.patch.object(appearance, "accessibility_settings", return_value=None), \
             mock.patch.object(appearance, "color_scheme", return_value="light"):
            self.assertEqual(appearance.surface_treatment(), "light")

    def test_accessibility_policy_resolves_translucency_to_opaque(self):
        treatment = mock.Mock()
        treatment.get_string.return_value = "glass"
        treatment.get_boolean.return_value = True
        treatment.get_user_value.return_value = object()
        accessibility = mock.Mock()
        accessibility.get_boolean.return_value = False
        with mock.patch.object(appearance, "treatment_settings", return_value=treatment), \
             mock.patch.object(appearance, "accessibility_settings", return_value=accessibility), \
             mock.patch.object(appearance, "color_scheme", return_value="light"):
            self.assertEqual(appearance.surface_treatment(), "light")

        treatment.get_boolean.return_value = False
        accessibility.get_boolean.return_value = True
        with mock.patch.object(appearance, "treatment_settings", return_value=treatment), \
             mock.patch.object(appearance, "accessibility_settings", return_value=accessibility), \
             mock.patch.object(appearance, "color_scheme", return_value="dark"):
            self.assertEqual(appearance.surface_treatment(), "dark")

    @unittest.skipIf(Service is None, "packaged runtime provides PyGObject")
    def test_changes_coalesce_and_do_not_wake_stopped_runtime(self):
        service = Service.__new__(Service)
        service.appearance_lock = threading.Lock()
        service.appearance_pending = None
        service.appearance_worker = None
        service.engine = mock.Mock()
        service.engine.status.return_value = {"session": "RUNNING"}
        entered, release = threading.Event(), threading.Event()

        def apply(value):
            if value == "dark":
                entered.set()
                self.assertTrue(release.wait(3))

        service.engine.sync_host_appearance.side_effect = apply
        settings = mock.Mock()
        with mock.patch("luma_android.service.surface_treatment", return_value="dark"):
            service.on_appearance_changed(settings, "color-scheme")
        worker = service.appearance_worker
        self.assertTrue(entered.wait(3))
        with mock.patch("luma_android.service.surface_treatment", return_value="glass"):
            service.on_appearance_changed(settings, "surface-treatment")
            service.on_appearance_changed(settings, "surface-treatment")
        self.assertIs(service.appearance_worker, worker)
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(service.engine.sync_host_appearance.call_args_list,
                         [mock.call("dark"), mock.call("glass")])
        service.engine.reset_mock()
        service.engine.status.return_value = {"session": "STOPPED"}
        service.appearance_pending = "dark"
        service.sync_appearance_changes()
        service.engine.sync_host_appearance.assert_not_called()
        service.engine.ensure_ready.assert_not_called()
