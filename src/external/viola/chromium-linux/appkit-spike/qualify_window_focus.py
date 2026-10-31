# SPDX-License-Identifier: GPL-3.0-only
"""Physical new-window accelerators followed by immediate address typing."""
from gi.repository import GLib
from qualify_native_windows import WindowQualification as Base
from private_input import PrivateInput


class WindowQualification(Base):
    def __init__(self, manager, report, complete):
        self.manager, self.report, self.complete = manager, report, complete
        self.device = PrivateInput(); self.device.start()
        self.report['checks'] = []
        self.primary = manager.primary['window']
        self.wait(lambda:self.primary.get_mapped() and self.primary.page_input.valid_identity(),self.startup)

    def startup(self):
        self.check('startup_address_focused',self.primary.address_focused())
        self.type_into(self.primary,'startup',self.normal)

    def type_into(self, window, text, then):
        self.check(text+'_initial_address_focus',window.address_focused())
        for i,ch in enumerate(text):
            GLib.timeout_add(100*i+150,lambda char=ch:self.device.key(ord(char)) or False)
        def verify():
            try:
                self.check(text+'_typing_goes_to_address',window.address.get_text()==text)
                self.check(text+'_focus_survives_page_updates',window.address_focused())
                then()
            except Exception as error:self.finish(str(error))
            return False
        GLib.timeout_add(100*len(text)+800,verify)

    def normal(self):
        self.device.chord(0xffe3,ord('n'))
        self.wait(lambda:len(self.manager.contexts)==2 and all(c['window'].get_mapped() and c['window'].page_input.valid_identity() for c in self.manager.contexts.values()),self.normal_ready)

    def normal_ready(self):
        self.second=next(c['window'] for c in self.manager.contexts.values() if c['window'] is not self.primary)
        self.check('ctrl_n_regular_window',not self.second.services.state.get('incognito'))
        self.type_into(self.second,'regular',self.private)

    def private(self):
        self.device.chord((0xffe3,0xffe1),ord('n'))
        self.wait(lambda:len(self.manager.contexts)==3 and any(c['window'].services and c['window'].services.state and c['window'].services.state.get('incognito') and c['window'].get_mapped() for c in self.manager.contexts.values()),self.private_ready)

    def private_ready(self):
        window=next(c['window'] for c in self.manager.contexts.values() if c['window'].services.state.get('incognito'))
        self.check('ctrl_shift_n_private_window',bool(window.services.state.get('incognito')))
        self.private_window=window
        self.private_tabs=self.tab_ids(window)
        self.private_initial=window.services.state['activeTabId']
        self.check('private_initial_blank_selected',window.services.state.get('activeUrl')=='about:blank')
        self.type_into(window,'private',self.private_new_tab)

    @staticmethod
    def tab_ids(window):
        return {tab['id'] for section in ('today','pinned','favorites')
                for tab in window.services.state.get(section,[])}

    def private_new_tab(self):
        # CtrlT is an intentional additional tab, not a request to refocus the
        # existing initial blank tab. It must create exactly one tab/window.
        self.device.chord(0xffe3,ord('t'))
        self.wait(lambda:self.private_window.services.state.get('activeTabId')!=self.private_initial
                  and self.private_window.page_input.valid_identity(),self.private_new_tab_ready)

    def private_new_tab_ready(self):
        current=self.tab_ids(self.private_window)
        self.check('private_ctrl_t_creates_exactly_one_tab',
                   len(current-self.private_tabs)==1 and self.private_tabs.issubset(current))
        self.check('private_ctrl_t_does_not_create_window',len(self.manager.contexts)==3)
        self.type_into(self.private_window,'private-new-tab',lambda:self.finish())

    def finish(self,error=None):
        self.report['window_states']=[{'mapped': c['window'].get_mapped(), 'identity': c['window'].page_input.valid_identity(), 'incognito': c['window'].services.state.get('incognito'), 'activeUrl': c['window'].services.state.get('activeUrl')} for c in self.manager.contexts.values()]
        self.report['completed']=True
        if error:self.report['error']=error
        self.device.close()
        self.complete()
