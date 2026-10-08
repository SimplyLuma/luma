# SPDX-License-Identifier: GPL-3.0-only
"""Real Mini routing, live-page adoption and OAuth-style popup lifecycle."""
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from gi.repository import GLib, Gtk
from qualify_native_windows import WindowQualification
from private_input import PrivateInput


class MiniQualification(WindowQualification):
    def __init__(self, manager, report, complete):
        self.manager, self.report, self.complete = manager, report, complete
        self.report['checks'] = []
        self.primary = manager.primary['window']
        self.device = PrivateInput();self.device.start()
        self.wait(lambda:self.primary.page_input.valid_identity(),self.external)

    def evaluate(self, window, source):
        result = self.manager.engine.call('Runtime.evaluate',{'expression':source,'returnByValue':True,'userGesture':True},session=window.page_input.session)
        if result.get('exceptionDetails'):raise AssertionError(result['exceptionDetails'])
        return result.get('result',{}).get('value')

    def mini_ready(self):
        return len(self.manager.contexts)==2 and all(c['window'].page_input.valid_identity() and c['window'].get_mapped() for c in self.manager.contexts.values())

    def external(self):
        parts=urlsplit(self.primary.state['activeUrl'])
        self.origin=f'{parts.scheme}://{parts.netloc}'
        self.original=self.primary.page_input.target
        def open_link():
            self.evaluate(self.primary,'document.cookie="violaMiniProof=shared; path=/";true')
            return self.manager.engine.open_external(self.origin+'/layout?title=Authenticate%20with%20Viola')
        self.primary.submit(open_link,lambda ok:self.check('external_link_forwarded_to_existing_chromium',ok))
        self.wait(self.mini_ready,self.external_ready)

    def external_ready(self):
        self.mini=next(c['window'] for c in self.manager.contexts.values() if c['window'] is not self.primary)
        self.check('external_link_uses_compact_native_window',self.mini.mini)
        self.check('primary_page_untouched',self.primary.page_input.target==self.original)
        self.check('shared_page_bar_and_primary_open_action_without_sidebar',
            not any(w.get_visible() for w in (self.mini.sidebar_bin,self.mini.sidebar_layout.edge,self.mini.toggle,self.mini.new_tab,self.mini.bookmark))
            and not self.mini.toolbar.get_mapped()
            and self.mini.responsive.center.bar.get_mapped()
            and self.mini.menu_button.get_mapped()
            and self.mini.responsive.expand.has_css_class('primary')
            and self.mini.responsive.expand is self.mini.responsive.center.bar_row.get_last_child())
        self.check('native_titlebar_and_page_frame_retained',self.mini.title_bar.get_visible() and self.mini.island.get_visible())
        self.target=self.mini.page_input.target
        self.mini.submit(lambda:self.evaluate(self.mini,'window.violaLivePageProof="retained";document.cookie.includes("violaMiniProof=shared")'),self.shared_session)

    def shared_session(self, shared):
        self.check('mini_shares_existing_profile_cookie',shared)
        self.mini.present()
        self.mini.set_default_size(460,740)
        GLib.timeout_add(900, self.narrow_bar)

    def narrow_bar(self):
        from capture_native_widget import capture
        ok,bar=self.mini.responsive.center.bar.compute_bounds(self.mini)
        self.report['narrow_geometry']=dict(width=self.mini.get_width(),bar=[bar.get_x(),bar.get_width()] if ok else None,
            address_mapped=self.mini.address.get_mapped(),expand_mapped=self.mini.responsive.expand.get_mapped(),
            forward_visible=self.mini.forward.get_visible(),reload_visible=self.mini.reload.get_visible())
        self.check('shared_bar_and_labelled_open_action_fit_460px_popup',
            self.mini.get_width()==460 and ok and bar.get_x()>=0 and
            bar.get_x()+bar.get_width()<=self.mini.get_width()+1 and
            self.mini.responsive.expand.get_mapped() and self.mini.address.get_mapped())
        capture(self.mini,Path('/tmp/viola-mini-460.png'))
        self.report['narrow_snapshot']='/tmp/viola-mini-460.png'
        self.mini.set_default_size(600,740)
        GLib.timeout_add(900,self.capture_and_expand)
        return False

    def capture_and_expand(self):
        def capture_monitor():
            path='/tmp/viola-mini-monitor.png'
            subprocess.run(['python3',str(Path(__file__).with_name('capture_private_display.py')),'--output',path],check=True,timeout=20)
            return path
        self.mini.submit(capture_monitor,self.expand)
        return False

    def expand(self, path):
        self.report['monitor_snapshot']=path
        # Exercise the actual end action and retain DOM state after adoption.
        self.mini.responsive.expand.emit('clicked')
        self.wait(lambda:len(self.manager.contexts)==1 and self.primary.page_input.target==self.target and self.primary.page_input.valid_identity(),self.adopted)
        return False

    def adopted(self):
        self.primary.submit(lambda:self.evaluate(self.primary,'window.violaLivePageProof'),self.open_popup)

    def open_popup(self, proof):
        self.check('expand_transfers_exact_live_page_without_reload',proof=='retained')
        source='window.addEventListener("message",e=>{if(e.origin===location.origin)window.violaAuthResult=e.data});window.open('+json.dumps(self.origin+'/layout?title=Sign%20in')+',"viola-auth","width=520,height=640");true'
        self.primary.submit(lambda:self.evaluate(self.primary,source))
        self.wait(self.mini_ready,self.popup_ready)

    def popup_ready(self):
        popup=next(c['window'] for c in self.manager.contexts.values() if c['window'] is not self.primary)
        self.check('scripted_auth_popup_uses_compact_shell',popup.mini)
        popup.submit(lambda:self.evaluate(popup,'window.opener.postMessage("auth-complete",location.origin);window.close();true'))
        self.wait(lambda:len(self.manager.contexts)==1,self.popup_closed)

    def popup_closed(self):
        self.primary.submit(lambda:self.evaluate(self.primary,'window.violaAuthResult'),self.callback)

    def callback(self, value):
        self.check('popup_preserves_opener_callback_and_closes_only_itself',value=='auth-complete' and self.manager.engine.process.poll() is None)
        self.primary.present();self.primary.page.grab_focus()
        self.device.chord((0xffe3,0xffe9),ord('n'))
        self.wait(self.mini_ready,self.shortcut_ready)

    def shortcut_ready(self):
        mini=next(c['window'] for c in self.manager.contexts.values() if c['window'] is not self.primary)
        self.check('shortcut_opens_blank_compact_window',mini.mini and mini.state.get('activeUrl')=='about:blank')
        mini.present();mini.page.grab_focus()
        self.device.chord(0xffe3,ord('w'))
        self.wait(lambda:len(self.manager.contexts)==1,self.finished)

    def finished(self):
        self.check('close_mini_keeps_regular_browser_alive',self.manager.engine.process.poll() is None and self.primary.get_mapped())
        GLib.timeout_add(400,lambda:self.finish() or False)
