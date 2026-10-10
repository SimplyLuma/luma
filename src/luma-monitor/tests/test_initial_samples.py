# SPDX-License-Identifier: Apache-2.0
"""Real proc fixtures must publish activity before optional services finish."""
from types import SimpleNamespace
from collections import deque
import unittest
from unittest.mock import Mock, patch

from luma_monitor.application import MonitorWindow
from luma_monitor.live_data import resource_facts
import test_model as model


class SampleLabel:
    """Headless label adapter; the GTK runtime repeats these cases with Gtk.Label."""
    def __init__(self): self.text=''
    def set_label(self, text): self.text=text
    def get_label(self): return self.text


class InitialSampleTests(unittest.TestCase):
    label_factory=SampleLabel

    def window(self, totals=None):
        window=SimpleNamespace(fixture=False, closed=False, working=True,
            generation=2, resource='cpu', totals=totals or {}, sample_failed=False,
            apps=[], host=SimpleNamespace(modal=True), hero_title=self.label_factory(),
            hero_subtitle=self.label_factory(), _visible_apps=lambda: [])
        window._hero_text=lambda: MonitorWindow._hero_text(window)
        return window

    def test_initial_host_failure_replaces_loading_and_retires_worker(self):
        window=self.window()
        self.assertEqual(window._hero_text()[1], 'Loading…')
        with patch('luma_monitor.application.Toast.show') as toast:
            self.assertIs(MonitorWindow._sampled(window,2,None,'Host sampler refused'),False)
        self.assertFalse(window.working)
        self.assertTrue(window.sample_failed)
        self.assertEqual(window.hero_title.get_label(), 'System activity is unavailable.')
        self.assertEqual(window.hero_subtitle.get_label(), 'Trying again…')
        toast.assert_called_once()
        self.assertEqual(window.totals,{})

    def test_refresh_failure_marks_preserved_measurement_as_stale(self):
        window=self.window({'cpu':31})
        with patch('luma_monitor.application.Toast.show'):
            MonitorWindow._sampled(window,2,None,'Host unavailable')
        self.assertEqual(window.totals['cpu'],31)
        self.assertEqual(window.hero_subtitle.get_label(),'Showing the last reading. Retrying…')

    def test_repeated_failures_do_not_repeat_toast(self):
        window=self.window()
        with patch('luma_monitor.application.Toast.show') as toast:
            MonitorWindow._sampled(window,2,None,'Host unavailable')
            MonitorWindow._sampled(window,2,None,'Host unavailable')
        toast.assert_called_once()

    def test_obsolete_failure_does_not_replace_current_activity(self):
        window=self.window({'cpu':31})
        with patch('luma_monitor.application.Toast.show') as toast:
            MonitorWindow._sampled(window,1,None,'Old request failed')
        self.assertFalse(window.sample_failed)
        toast.assert_not_called()

    def test_real_recovered_sample_clears_failure_even_while_menu_is_open(self):
        files=model.Samples();files.setUp();self.addCleanup(files.tearDown)
        files.sampler.sample(memory=False,now=1)
        files.write(files.proc/'stat','cpu 50 0 20 130 0 0 0 0\ncpu0 50 0 20 130 0 0 0 0\n')
        snapshot=files.sampler.sample(memory=True,now=2)
        snapshot['advanced_facts']=resource_facts(snapshot,proc=files.proc,sys=files.sys)
        window=self.window();window.sample_failed=True
        window.sampler=files.sampler;window.properties={};window.selected=None
        window.histories={key:deque() for key in ('cpu','mem','disk','net','en')}
        window._render_sidebar=Mock();window._chart_max=lambda _:100;window.chart=Mock()
        window._menu_widget=None
        MonitorWindow._sampled(window,2,snapshot,None)
        self.assertFalse(window.sample_failed)
        self.assertEqual(window.totals['cpu'],snapshot['cpu'])
        self.assertIn('cores in use',window.hero_subtitle.get_label())
        self.assertNotIn('Retrying',window.hero_subtitle.get_label())

    def test_real_process_and_cpu_samples_precede_optional_service_read(self):
        files = model.Samples()
        files.setUp()
        queued = []
        self.addCleanup(files.tearDown)
        window = SimpleNamespace(fixture=False, closed=False, working=False,
            generation=0, catalog=SimpleNamespace(refresh_running=Mock(), close=Mock()),
            sampler=files.sampler, _sampled=Mock())

        def advance(_seconds):
            files.write(files.proc / 'stat', 'cpu 50 0 20 130 0 0 0 0\ncpu0 50 0 20 130 0 0 0 0\n')
            files.write(files.proc / '42/stat', model.process_stat(42, 30))

        def optional_read():
            # This is where a slow UPower activation previously held all
            # process and CPU data out of the UI. Both real samples are queued.
            self.assertEqual(len(queued), 2)
            self.assertEqual(len(queued[0][2]['processes']), 2)
            self.assertIsNone(queued[0][2]['cpu'])
            self.assertIsNotNone(queued[1][2]['cpu'])
            self.assertTrue(window.working)
            return None

        class Worker:
            def __init__(self, *, target, **_kwargs): self.target = target
            def start(self): self.target()

        with patch('luma_monitor.application.threading.Thread', Worker), \
             patch('luma_monitor.application.GLib.idle_add', side_effect=lambda *args: queued.append(args)), \
             patch('luma_monitor.application.time.sleep', side_effect=advance), \
             patch('luma_monitor.integration.battery_info', side_effect=optional_read), \
             patch('luma_monitor.integration.performance_profile', return_value=None):
            MonitorWindow._sample(window)
        self.assertEqual(len(queued), 3)
        self.assertFalse(queued[0][-1])
        self.assertFalse(queued[1][-1])
        # Full proportional-memory facts still reach the normal final callback.
        self.assertEqual(queued[2][2]['rows'][0]['memory'], 200 * 1024)
        self.assertIsNone(queued[2][3])


if __name__ == '__main__': unittest.main()
