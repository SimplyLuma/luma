"""Luma Connect: your Luma account, as this computer sees it.

A person has one Luma account. From this computer they see what it keeps in
step, the devices on it, and the phones this computer works with:

* Luma Connect sync (calendar, notes, contacts, photos, clocks, weather, music
  servers, books, messages and call history) is run by `luma-connect-sync`
  through `cloud_sync`. Its account is the account the app is about.
* Android phones pair with this computer directly (ADR-021); no account needed.
* The Connect daemon relays phone messages and calls between Luma devices. It
  has its own sign-in, shown with the phones it serves rather than as a second
  account.

Every switch that changes what leaves this computer says what it will do
before it does it, using the sync engine's own words. Background refreshes
never rebuild what is on screen unless something a person can see changed.
"""
import json
import os
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Adw,Gdk,Gio,GLib,Gtk,Pango
from luma_appkit import (add_style_builder, AppWindow,AppContext,PresentationMode,CommandRegistry,Island,
    IslandSplitView,NavigationSidebar,NavigationRow,EmptyState,install_appkit,
    Card,LayerHost,TextField,TypeLabel,TextButton)
try:
    from luma_appkit import Avatar
except ImportError:  # an older kit; the account page then shows no portrait
    Avatar=None
from .phone_contract import BUS,PATH
from .cloud_sync import CloudSync,DEFAULT_HUB,HUB_CONNECT_PAGE,device_name
from .companion_contract import DEFAULT_DESKTOP_TO_PHONE,DEFAULT_PHONE_TO_DESKTOP
from .connect_view import (COUNT_WORDS,device_icon,discoverable_subtitle,profile_subtitles,provider_name,
    format_phone as connect_format_phone,
    service_subtitle,sign_in_subtitle,stable,when_text)

APP_ID='org.projectluma.Connect'
OPTIONAL='Luma Connect is optional. Everything on this computer keeps working without it.'
# The pages, in the sidebar's order. The account itself heads the sidebar.
PANELS=(('account','Account','luma-connect-user-symbolic'),('sync','Syncing','luma-connect-sync-symbolic'),
    ('devices','Devices','luma-connect-laptop-symbolic'),('phones','Phones','luma-connect-phone-symbolic'))
CLOUD_REFRESH_SECONDS=60
# Each synced service is shown with the app that owns it.
SERVICE_APPS={'calendar':'org.projectluma.Calendar','notes':'org.projectluma.Notes','contacts':'org.projectluma.Contacts',
    'photos':'org.projectluma.Photos','world-clocks':'org.projectluma.Clock','weather-places':'org.projectluma.Weather',
    'messages':'org.projectluma.Messages','calls':'org.projectluma.Phone','leaf-books':'org.projectluma.Leaf',
    'tide-sources':'org.projectluma.Tide'}


def tile(icon_name):
    """A 32px rounded square holding a 16px glyph, level with 32px app icons."""
    holder=Adw.Bin(width_request=32,height_request=32,valign=Gtk.Align.CENTER,halign=Gtk.Align.CENTER,css_classes=['luma-connect-tile'])
    holder.set_child(Gtk.Image(icon_name=icon_name,pixel_size=16,halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER))
    return holder

def _launch(uri,window):
    Gtk.UriLauncher.new(uri).launch(window,None,None,None)


class Page(Gtk.ScrolledWindow):
    """One page of the content island: a title, a line about it, then its groups."""

    def __init__(self,title,lede='',*,suffix=None,back=None):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER,vexpand=True,hexpand=True)
        self.add_css_class('luma-connect-page')
        self.box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=22,margin_top=24,margin_bottom=32,margin_start=28,margin_end=28)
        clamp=Adw.Clamp(maximum_size=640,tightening_threshold=520,child=self.box)
        self.set_child(clamp)
        if title:
            head=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4)
            line=Gtk.Box(spacing=10)
            if back is not None:line.append(back)
            heading=Gtk.Label(label=title,xalign=0,hexpand=True,wrap=True)
            heading.add_css_class('title-2');heading.add_css_class('luma-connect-page-title')
            heading.update_property([Gtk.AccessibleProperty.LABEL],[title])
            line.append(heading)
            if suffix is not None:
                suffix.set_valign(Gtk.Align.CENTER);line.append(suffix)
            head.append(line)
            if lede:
                text=Gtk.Label(label=lede,xalign=0,wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR)
                text.add_css_class('dim-label');text.add_css_class('luma-connect-lede')
                head.append(text)
            self.box.append(head)

    def add(self,widget):
        self.box.append(widget)


class PhonesMixin:
    """Android phones paired through Luma Connect (ADR-021). No account is needed."""

    def _call(self,method,signature,values,done=None,timeout=15000,trouble=None):
        if not self.proxy or self.closed:return
        def finished(proxy,result):
            if self.closed:return
            try:value=proxy.call_finish(result).unpack()
            except GLib.Error:
                if trouble!='':self._say(trouble or 'That didn’t work. Try again in a moment.')
                if done:done(None)
                return
            if done:done(value)
            self._refresh()
        self.proxy.call(method,GLib.Variant(signature,values) if values is not None else None,
            Gio.DBusCallFlags.NONE,timeout,None,finished)

    def _phones(self,page,mobile):
        state=self.state or {};enabled=bool(state.get('enabled'))
        if not enabled:
            group=Adw.PreferencesGroup();page.add(group)
            row=Adw.SwitchRow(title='Luma Connect',subtitle='Turn it on to use a phone with this computer.',active=False)
            row.set_sensitive(self.state is not None and not state.get('busy'))
            row.connect('notify::active',lambda widget,_p:widget.get_active() and self._invoke('SetEnabled','(b)',(True,)))
            group.add(row)
        devices=state.get('companion_devices') or []
        if not devices:
            empty=EmptyState('No phones yet','Pair an Android phone to see its notifications and texts here, and to pass copied text and files back and forth.',
                'luma-connect-phone-symbolic',primary=('Pair a Phone…',self._pair_phone) if enabled else None,compact=True)
            frame=Gtk.Box(css_classes=['luma-connect-empty-card']);frame.append(empty);page.add(frame)
        transfers=state.get('companion_transfers') or []
        for device in devices:self._phone_group(page,device,[row for row in transfers if row['fingerprint']==device['fingerprint']],enabled)

    def _phone_group(self,page,device,transfers,enabled):
        peer=device['fingerprint'];outgoing=set(device.get('outgoing') or []);incoming=set(device.get('incoming') or [])
        name=device.get('name') or 'Phone'
        group=Adw.PreferencesGroup();page.add(group)
        status=device.get('status') or {}
        details=[]
        if not device.get('reachable'):details.append('Not nearby')
        if isinstance(status.get('battery'),int):details.append(f"{status['battery']}%"+(' charging' if status.get('charging') else ''))
        network={'wifi':'Wi-Fi','cellular':'Mobile data','none':'Offline'}.get(status.get('network'))
        if network and device.get('reachable'):details.append(network)
        if device.get('seen_at') and not device.get('reachable'):details.append(when_text(device['seen_at'],prefix='Seen '))
        head=Adw.ActionRow(title=name,subtitle=' · '.join(details) or (device.get('model') or ''),use_markup=False)
        head.add_css_class('luma-connect-phone-head')
        head.add_prefix(tile('luma-connect-phone-symbolic'))
        more=Gtk.MenuButton(icon_name='luma-connect-more-symbolic',valign=Gtk.Align.CENTER,tooltip_text=f'More for {name}')
        more.add_css_class('flat')
        menu=Gio.Menu();menu.append(f'Remove {name}…','win.remove-phone::'+peer);more.set_menu_model(menu)
        self._ensure_action('remove-phone',lambda target:self._confirm_remove(target,self._phone_name(target)))
        head.add_suffix(more)
        group.add(head)
        reachable=enabled and device.get('reachable')
        actions=Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,min_children_per_line=2,max_children_per_line=5,
            column_spacing=6,row_spacing=6,homogeneous=True,margin_top=10,margin_bottom=10,margin_start=12,margin_end=12)
        actions.add_css_class('luma-connect-actions')
        for label,icon,capability,handler in (('Ring','luma-connect-ring-symbolic','device.ring',lambda:self._ring(peer,name)),
                                         ('Send Text','luma-connect-clipboard-symbolic','clipboard.write',lambda:self._send_clipboard(peer)),
                                         ('Send File','luma-connect-send-symbolic','files.write',lambda:self._send_file(peer)),
                                         ('Hotspot','luma-connect-wifi-symbolic','hotspot.request',lambda:self._hotspot(peer,name)),
                                         ('Messages','luma-connect-message-symbolic','messages.read',self._open_messages),
                                         ('Webcam','luma-connect-webcam-symbolic','camera.stream',lambda:self._media(peer,'CompanionStartCamera',name)),
                                         ('Screen','luma-connect-screen-symbolic','screen.view',lambda:self._media(peer,'CompanionShowScreen',name)),
                                         ('Calls','luma-connect-call-symbolic','bluetooth.bond',lambda:self._set_up_calls(peer,name)),
                                         ('Power Mode','luma-connect-zap-symbolic','screen.view',lambda:self._power_mode(peer,device,state_power=(self.state.get('companion_power') or {}).get(peer)))):
            if capability not in outgoing:continue
            content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4,halign=Gtk.Align.CENTER)
            content.append(Gtk.Image(icon_name=icon,pixel_size=16))
            caption=Gtk.Label(label=label,ellipsize=Pango.EllipsizeMode.END,justify=Gtk.Justification.CENTER)
            caption.add_css_class('caption');content.append(caption)
            button=Gtk.Button(child=content,sensitive=bool(reachable),tooltip_text=label)
            button.update_property([Gtk.AccessibleProperty.LABEL],[label])
            button.add_css_class('luma-connect-action')
            button.connect('clicked',lambda _b,handler=handler:handler());actions.append(button)
        if actions.get_first_child() is not None:
            group.add(Gtk.ListBoxRow(activatable=False,selectable=False,child=actions))
        for transfer in transfers[-3:]:
            size=transfer.get('size') or 0
            progress={'queued':'Waiting to send','sending':f"Sending · {int(100*transfer['sent']/size) if size else 0}%",
                'complete':'Sent','failed':'Couldn’t send. Make sure your phone is nearby and try again.','cancelled':'Cancelled'}.get(transfer['state'],'')
            row=self._row(group,transfer['name'],progress)
            if transfer['state'] in {'queued','sending'}:
                cancel=Gtk.Button(label='Cancel',valign=Gtk.Align.CENTER)
                cancel.connect('clicked',lambda _b,t=transfer['transfer']:self._call('CancelCompanionTransfer','(s)',(t,)))
                row.add_suffix(cancel)
        allowed=Adw.ExpanderRow(title='What each device can do',use_markup=False,
            subtitle=f"{len(incoming)} for your phone · {len(outgoing)} for this computer")
        for title,grants,labels in ((f'{name} can',incoming,PHONE_TO_DESKTOP_LABELS),('This computer can',outgoing,DESKTOP_TO_PHONE_LABELS)):
            text=self._grant_text(grants,labels)
            row=Adw.ActionRow(title=title,subtitle=text or 'Nothing yet',use_markup=False);row.set_subtitle_lines(0)
            allowed.add_row(row)
        group.add(allowed)

    def _phone_name(self,peer):
        for device in (self.state or {}).get('companion_devices') or []:
            if device.get('fingerprint')==peer:return device.get('name') or 'this phone'
        return 'this phone'

    def _ensure_action(self,name,callback):
        if self.lookup_action(name) is None:
            action=Gio.SimpleAction.new(name,GLib.VariantType.new('s'))
            action.connect('activate',lambda _a,parameter:callback(parameter.get_string()))
            self.add_action(action)

    @staticmethod
    def _grant_text(grants,labels):
        return ', '.join(label for capability,label in labels if capability in grants)

    def _confirm_remove(self,peer,name):
        dialog=Adw.AlertDialog(heading=f'Remove {name}?',body='It stops working with this computer right away. Anything it already sent stays here. You can pair it again later.')
        dialog.add_response('cancel','Cancel');dialog.add_response('remove','Remove')
        dialog.set_response_appearance('remove',Adw.ResponseAppearance.DESTRUCTIVE);dialog.set_close_response('cancel')
        dialog.connect('response',lambda _d,response:self._call('RemoveCompanion','(s)',(peer,)) if response=='remove' else None)
        dialog.present(self)

    def _invoke_phone(self,peer,capability,payload,done=None):
        def finished(value):
            if value is None:return
            try:receipt=json.loads(value[0])
            except ValueError:receipt=None
            accepted=isinstance(receipt,dict) and receipt.get('state')=='complete' and not (receipt.get('result') or {}).get('error')
            if not accepted:self._say(PHONE_TROUBLE)
            if done:done(accepted)
        self._call('CompanionInvoke','(sss)',(peer,capability,json.dumps(payload)),finished,trouble=PHONE_TROUBLE)

    def _ring(self,peer,name):
        def rang(accepted):
            if not accepted:return
            dialog=Adw.AlertDialog(heading=f'Ringing {name}',body='Follow the sound. It stops on its own after a little while.')
            dialog.add_response('stop','Stop Ringing');dialog.set_close_response('stop')
            dialog.connect('response',lambda *_:self._invoke_phone(peer,'device.ring',{'ring':False}))
            dialog.present(self)
        self._invoke_phone(peer,'device.ring',{'ring':True},rang)

    def _hotspot(self,peer,name):
        self._say(f'Check {name}: tap the notification to turn on its hotspot. This computer joins it when it appears.')
        def finished(value):
            if value is None:return
            self._say({'joined':f'Connected through {name}.',
                       'learned':'Connected. Next time this computer joins that hotspot by itself.',
                       'not-found':f'No hotspot from {name} showed up. Turn it on, then choose it in the Wi-Fi menu.',
                       'refused':PHONE_TROUBLE}.get(value[0],PHONE_TROUBLE))
        self._call('CompanionRequestHotspot','(s)',(peer,),finished,timeout=120000,trouble=PHONE_TROUBLE)

    def _open_messages(self):
        # Messages lists every paired phone that allowed texts by itself (ADR-022); there is nothing to choose here.
        info=Gio.DesktopAppInfo.new('org.projectluma.Messages.desktop')
        try:
            if info is None or not info.launch([],None):raise GLib.Error('Messages unavailable')
        except GLib.Error:
            self._say('Messages isn’t installed on this computer.')

    def _media(self,peer,method,name):
        what='camera' if method=='CompanionStartCamera' else 'screen'
        def finished(value):
            if value is None:return
            self._say({'needs-user':f'Tap the notification on {name} to start. Android asks for this every time.',
                       'started':f'Starting {name}’s {what}.',
                       'refused':PHONE_TROUBLE}.get(value[0],PHONE_TROUBLE))
        self._call(method,'(s)',(peer,),finished,trouble=PHONE_TROUBLE)

    def _set_up_calls(self,peer,name):
        self._say(f'On {name}, tap the notification to pair for calls, then check that both screens show the same code.')
        def finished(value):
            if value is None:return
            self._say({'bonded':f'Calls from {name} now ring in Phone on this computer. Audio stays on the phone until you choose this computer.',
                       'not-found':f'{name} didn’t pair in time. Try again and confirm the code on both screens.',
                       'ambiguous':'More than one device paired at the same time, so nothing was set up. Try again.',
                       'not-a-phone':'That device isn’t a phone, so it wasn’t set up for calls.',
                       'call-active':'Finish the call in progress, then try again.',
                       'refused':PHONE_TROUBLE}.get(value[0],'Bluetooth calls aren’t available on this computer.'))
        self._call('CompanionSetUpCalls','(s)',(peer,),finished,timeout=180000,trouble=PHONE_TROUBLE)

    def _power_mode(self,peer,device,state_power=None):
        """Wireless debugging lets this computer show and control the phone fully and open apps in their own windows."""
        name=device.get('name') or 'your phone'
        dialog=Adw.AlertDialog(heading='Power Mode',
            body=(f'Power Mode lets this computer control {name} with its mouse and keyboard, play its sound here, and open its apps in their own windows. '
                  'It uses Android’s Wireless debugging, which you turn on in Developer options. No root is needed, and you can turn it off at any time on the phone.'))
        ready=(state_power or {}).get('state')=='ready'
        dialog.add_response('close','Close')
        if ready:dialog.add_response('open','Show and Control')
        dialog.add_response('pair','Set Up Again' if ready else 'Set Up')
        dialog.set_close_response('close')
        def responded(_dialog,response):
            if response=='open':
                self._call('CompanionPowerModeOpen','(ss)',(peer,''),lambda value:value and value[0]!='launched' and self._say(
                    {'scrcpy-missing':'Install scrcpy to use Power Mode.','not-set-up':'Set up Power Mode first.'}.get(value[0],'Power Mode couldn’t reach the phone. Check that Wireless debugging is still on.')))
            elif response=='pair':
                self._call('CompanionPowerModePair','(s)',(peer,),lambda value:value and self._power_qr(value[0],name),
                    trouble='Power Mode needs Android Debug Bridge (adb) on this computer.')
        dialog.connect('response',responded);dialog.present(self)

    def _power_qr(self,qr,name):
        dialog=Adw.Dialog(title='Scan with Wireless debugging',content_width=420)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12,margin_top=18,margin_bottom=18,margin_start=18,margin_end=18)
        steps=Gtk.Label(wrap=True,xalign=0,label=(f'On {name}, open Settings › Developer options › Wireless debugging, turn it on, '
            'then choose “Pair device with QR code” and scan this code. Keep both devices on the same Wi-Fi.'))
        box.append(steps)
        code=qr_code(qr)
        if code:box.append(code)
        else:box.append(Gtk.Label(selectable=True,wrap=True,label=qr))
        box.append(Gtk.Label(wrap=True,xalign=0,css_classes=['dim-label'],label='This computer connects on its own once the phone accepts. The code stops working after two minutes.'))
        dialog.set_child(box);dialog.present(self)

    def _send_clipboard(self,peer):
        clipboard=Gdk.Display.get_default().get_clipboard()
        def read(source,result):
            try:text=source.read_text_finish(result)
            except GLib.Error:text=None
            if not text:self._say('There’s no copied text to send.');return
            if len(json.dumps({'text':text}).encode())>60000:self._say('That’s too much text to send at once. Try sending it as a file.');return
            self._invoke_phone(peer,'clipboard.write',{'text':text},lambda ok:ok and self._say('Sent to your phone’s clipboard.'))
        clipboard.read_text_async(None,read)

    def _send_file(self,peer):
        chooser=Gtk.FileDialog(title='Send a file to your phone')
        def opened(dialog,result):
            try:path=dialog.open_finish(result).get_path()
            except GLib.Error:return
            if not path:self._say('Choose a file saved on this computer.');return
            self._call('CompanionSendFile','(ss)',(peer,path))
        chooser.open(self,None,opened)

    def _pair_phone(self):
        if self.pairing_dialog:self.pairing_dialog.present(self);return
        dialog=Adw.Dialog(title='Pair an Android Phone',content_width=460,content_height=640,follows_content_size=False)
        navigation=Adw.NavigationView();dialog.set_child(navigation)
        self.pairing_dialog=dialog;self.pairing_view=navigation;self.pairing_page=None;self.pairing_uri=None
        page=Adw.PreferencesPage()
        intro=Adw.PreferencesGroup(description='Choose what your phone and this computer may do for each other. You can change your mind later, on either device.')
        page.add(intro)
        checks={}
        for title,labels,defaults,key in (('Your phone can',PHONE_TO_DESKTOP_LABELS,DEFAULT_PHONE_TO_DESKTOP,'in'),
                                          ('This computer can',DESKTOP_TO_PHONE_LABELS,DEFAULT_DESKTOP_TO_PHONE,'out')):
            group=Adw.PreferencesGroup(title=title);page.add(group)
            for capability,label in labels:
                check=Gtk.CheckButton(active=capability in defaults,valign=Gtk.Align.CENTER)
                row=Adw.ActionRow(title=label,use_markup=False,activatable_widget=check);row.add_prefix(check)
                group.add(row);checks[(key,capability)]=check
        hint=Gtk.Label(wrap=True,justify=Gtk.Justification.CENTER,visible=False,margin_start=24,margin_end=24)
        start=Gtk.Button(label='Show Pairing Code',margin_top=12,margin_bottom=12,halign=Gtk.Align.CENTER)
        start.add_css_class('suggested-action');start.add_css_class('pill')
        def begin(*_):
            incoming=[c for (k,c),check in checks.items() if k=='in' and check.get_active()]
            outgoing=[c for (k,c),check in checks.items() if k=='out' and check.get_active()]
            if not incoming and not outgoing:
                hint.set_label('Choose at least one thing to share.');hint.set_visible(True);return
            start.set_sensitive(False);hint.set_visible(False)
            def started(value):
                start.set_sensitive(True)
                if self.pairing_dialog is not dialog:return
                if value is None:
                    hint.set_label('Pairing couldn’t start. Make sure Luma Connect is on and this computer is connected to a local network.')
                    hint.set_visible(True);return
                self.pairing_uri=value[0];self._pairing_code_page()
            self._call('StartCompanionPairing','(asas)',(incoming,outgoing),started,timeout=30000,trouble='')
        start.connect('clicked',begin)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);box.append(page);box.append(hint);box.append(start)
        page.set_vexpand(True)
        navigation.add(self._dialog_page(box,'Choose What to Share','choose'))
        def closed(*_):
            state=(self.state or {}).get('companion_pairing') or {}
            if state.get('state')=='waiting':self._call('CancelCompanionPairing','()',None)
            self.pairing_dialog=None
        dialog.connect('closed',closed)
        dialog.present(self)

    @staticmethod
    def _dialog_page(child,title,tag):
        toolbar=Adw.ToolbarView();toolbar.add_top_bar(Adw.HeaderBar());toolbar.set_content(child)
        return Adw.NavigationPage.new_with_tag(toolbar,title,tag)

    def _pairing_code_page(self):
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12,margin_start=24,margin_end=24,margin_top=12,margin_bottom=24)
        steps=Gtk.Label(label='On your phone, open Luma Connect and scan this code. Keep both devices on the same network.',wrap=True,justify=Gtk.Justification.CENTER)
        box.append(steps)
        code=qr_code(self.pairing_uri)
        if code:box.append(code)
        else:box.append(Gtk.Label(label='This computer can’t draw the code. Enter this link on your phone instead.',wrap=True,justify=Gtk.Justification.CENTER))
        link=Gtk.Label(label=self.pairing_uri,selectable=True,wrap=True,wrap_mode=Pango.WrapMode.WORD_CHAR,justify=Gtk.Justification.CENTER)
        link.add_css_class('dim-label')
        link.update_property([Gtk.AccessibleProperty.LABEL],['Pairing link'])
        if code:
            expander=Gtk.Expander(label='Can’t scan? Show the pairing link');expander.set_child(link);box.append(expander)
        else:box.append(link)
        self.pairing_status=Gtk.Label(label='Waiting for your phone…',wrap=True,justify=Gtk.Justification.CENTER)
        self.pairing_status.update_property([Gtk.AccessibleProperty.LABEL],['Pairing status'])
        box.append(self.pairing_status)
        scroll=Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,child=box,vexpand=True)
        self.pairing_view.push(self._dialog_page(scroll,'Scan with Your Phone','scan'))
        self.pairing_page='scan'
        self._companion_prompts()

    def _pairing_check_page(self,state):
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16,margin_start=24,margin_end=24,margin_top=24,margin_bottom=24,valign=Gtk.Align.CENTER)
        box.append(Gtk.Label(label=f"{state.get('name') or 'Your phone'} is paired. Check that your phone shows the same code.",wrap=True,justify=Gtk.Justification.CENTER))
        digits=state.get('sas') or ''
        sas=Gtk.Label(label=f'{digits[:3]} {digits[3:]}',selectable=True);sas.add_css_class('title-1')
        sas.update_property([Gtk.AccessibleProperty.LABEL],['Pairing code '+' '.join(digits)])
        box.append(sas)
        box.append(Gtk.Label(label='If the numbers are different, remove the phone. Something else may have answered.',wrap=True,justify=Gtk.Justification.CENTER))
        buttons=Gtk.Box(spacing=8,halign=Gtk.Align.CENTER)
        match=Gtk.Button(label='Codes Match');match.add_css_class('suggested-action')
        remove=Gtk.Button(label='Remove');remove.add_css_class('destructive-action')
        dialog=self.pairing_dialog
        match.connect('clicked',lambda *_:(self._call('CancelCompanionPairing','()',None),dialog.close()))
        remove.connect('clicked',lambda *_:(self._call('RemoveCompanion','(s)',(state['fingerprint'],)),
                                           self._call('CancelCompanionPairing','()',None),dialog.close()))
        buttons.append(remove);buttons.append(match);box.append(buttons)
        self.pairing_view.push(self._dialog_page(box,'Check the Code','check'))
        self.pairing_page='check'
        match.grab_focus()

    def _companion_prompts(self):
        state=self.state or {}
        pairing=state.get('companion_pairing') or {}
        if self.pairing_dialog and self.pairing_page=='scan':
            phase=pairing.get('state')
            if phase=='paired' and pairing.get('sas'):self._pairing_check_page(pairing)
            else:
                self.pairing_status.set_label({'waiting':'Waiting for your phone…',
                    'expired':'This code has expired. Go back and start again.',
                    'failed':'Pairing didn’t finish. Go back and start again.',
                    'cancelled':'Pairing was stopped. Go back and start again.',
                    'unavailable':'Pairing couldn’t start. Make sure this computer is connected to a local network.'}.get(phase,'Waiting for your phone…'))
        reply=state.get('companion_reply')
        if reply and not self.reply_dialog and self.reply_shown!=(reply['fingerprint'],reply['key'],reply['expires']):
            self.reply_shown=(reply['fingerprint'],reply['key'],reply['expires'])
            self._reply_prompt(reply)

    def _reply_prompt(self,reply):
        dialog=Adw.AlertDialog(heading=f"Reply to {reply.get('title') or 'this message'}",
            body=f"{reply.get('app') or 'A message'} on {reply.get('device') or 'your phone'}")
        entry=Gtk.Entry(placeholder_text='Your reply',activates_default=True,max_length=4096)
        entry.update_property([Gtk.AccessibleProperty.LABEL],['Your reply'])
        dialog.set_extra_child(entry)
        dialog.add_response('cancel','Cancel');dialog.add_response('send',reply.get('label') or 'Send')
        dialog.set_response_appearance('send',Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('send');dialog.set_close_response('cancel')
        def responded(_dialog,response):
            self.reply_dialog=None
            text=entry.get_text().strip()
            if response=='send' and text:
                self._invoke_phone(reply['fingerprint'],'notifications.act',{'key':reply['key'],'action':reply['action'],'text':text})
            else:self._call('CancelCompanionReply','()',None)
        dialog.connect('response',responded)
        self.reply_dialog=dialog;dialog.present(self)


PHONE_TROUBLE='Your phone didn’t answer. Make sure it’s nearby, awake and on the same network, then try again.'
PHONE_TO_DESKTOP_LABELS=(('notifications.mirror','Show its notifications on this computer'),
    ('device.status','Show its battery and connection here'),
    ('clipboard.write','Send copied text to this computer'),
    ('files.write','Send files to this computer'),
    ('links.open','Send links for you to open here'),
    ('media.mirror','Show what’s playing, so you can control it here'),
    ('clipboard.read','Get the text you copied on this computer'),
    ('device.ring','Ring this computer to find it'),
    ('input.control','Work as a touchpad and keyboard for this computer'),
    ('dnd.set','Keep Do Not Disturb in step with this computer'),
    ('auth.response','Answer approval requests from this computer'))
DESKTOP_TO_PHONE_LABELS=(('notifications.act','Answer and dismiss its notifications'),
    ('device.ring','Ring your phone to find it'),
    ('clipboard.write','Send copied text to your phone'),
    ('files.write','Send files to your phone'),
    ('links.open','Send links to your phone'),
    ('media.control','Play, pause and skip on your phone'),
    ('dnd.set','Keep Do Not Disturb in step with your phone'),
    ('hotspot.request','Ask to use your phone’s mobile connection'),
    ('auth.request','Ask you to approve things with your phone'),
    ('input.control','Type on your phone with this keyboard'),
    ('messages.read','Show your text messages in Messages'),
    ('messages.send','Send text messages through your phone'),
    ('camera.stream','Use your phone as a webcam'),
    ('screen.view','Show your phone’s screen here'),
    ('screen.control','Tap and type on your phone while its screen is shown'),
    ('bluetooth.bond','Take phone calls on this computer'))


def qr_code(uri):
    """Draws the pairing link as a QR code, or returns None when no QR encoder is installed."""
    from .qr import matrix as qr_matrix
    matrix=qr_matrix(uri)
    if matrix is None:return None
    area=Gtk.DrawingArea(content_width=260,content_height=260,halign=Gtk.Align.CENTER,accessible_role=Gtk.AccessibleRole.IMG)
    area.update_property([Gtk.AccessibleProperty.LABEL],['Pairing code to scan with your phone'])
    def draw(_area,cr,width,height):
        cells=len(matrix);scale=max(1,min(width,height)//cells)
        left,top=(width-cells*scale)//2,(height-cells*scale)//2
        # Scanners need dark modules on a light quiet zone in every theme, so the
        # code is drawn in fixed black and white rather than theme tokens.
        cr.set_source_rgb(1,1,1);cr.rectangle(left,top,cells*scale,cells*scale);cr.fill()
        cr.set_source_rgb(0,0,0)
        for y,row in enumerate(matrix):
            for x,dark in enumerate(row):
                if dark:cr.rectangle(left+x*scale,top+y*scale,scale,scale)
        cr.fill()
    area.set_draw_func(draw)
    return area


STYLE='''
.luma-connect-page-title { font-weight: 700; }
.luma-connect-lede { font-size: 13px; }
.luma-connect-hero-name { font-size: 22px; font-weight: 700; }
.luma-connect-hero-detail { font-size: 13px; }
.luma-connect-tile { border-radius: 9px; background: alpha(currentColor, .08); }
.luma-connect-sidebar-account { margin-bottom: 8px; }
.luma-navigation-sidebar .luma-navigation-list { padding-top: 6px; }
.luma-connect-action { padding: 10px 6px; border-radius: 12px; }
.luma-connect-empty-card { border-radius: 15px; background: alpha(currentColor, .035); padding: 18px 0; }
.luma-connect-welcome-title { font-size: 28px; font-weight: 750; }
.luma-connect-welcome-lede { font-size: 15px; }
.luma-connect-code { font-size: 30px; font-weight: 700; letter-spacing: 3px; font-feature-settings: "tnum"; }
.luma-connect-signout { margin-top: 4px; }
'''


class ConnectWindow(PhonesMixin,AppWindow):
    def __init__(self,application):
        handheld=AppContext.from_environment().presentation is PresentationMode.FULLSCREEN
        super().__init__(application=application,app_id=APP_ID,title='Luma Connect',
            icon_name=APP_ID,commands=CommandRegistry(()),
            default_width=940,default_height=660,minimum_width=360 if handheld else 560,minimum_height=440)
        self.state=None;self.proxy=None;self.closed=False;self.handheld=handheld;self.compact=handheld
        self.panel=self.recall('panel','account')
        if self.panel not in {key for key,*_ in PANELS}:self.panel='account'
        self.cloud_sync=CloudSync();self.cloud=None;self.cloud_problem='';self.cloud_loading=False;self.cloud_busy=set()
        self.syncing_now=False;self.phones_only=False;self.pairing_dialog=None;self.reply_dialog=None;self.reply_shown=None
        self.shape=None;self.page_key=None;self.rows={};self.email_text=''
        self.toasts=Adw.ToastOverlay()
        self.set_body(self.toasts)
        self.connect('close-request',self._close)
        self.breakpoint=Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 639px'))
        self.breakpoint.connect('apply',lambda *_:self._layout_changed(True))
        self.breakpoint.connect('unapply',lambda *_:self._layout_changed(False))
        self.add_breakpoint(self.breakpoint)
        # Nothing is shown until the account is known, so a signed-in person never
        # sees the sign-in screen flash past; a slow answer shows a quiet spinner.
        self.toasts.set_child(Island())
        GLib.timeout_add(450,self._loading)
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION,Gio.DBusProxyFlags.NONE,None,
            BUS,PATH,BUS,None,self._connected)
        self._refresh_cloud()
        self.cloud_timer=GLib.timeout_add_seconds(CLOUD_REFRESH_SECONDS,self._cloud_tick)

    def _close(self,*_):
        self.closed=True
        if self.cloud_timer:GLib.source_remove(self.cloud_timer);self.cloud_timer=0
        return False

    def _loading(self):
        if self.shape is None and not self.closed:
            island=Island()
            spinner=Adw.Spinner(halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER,vexpand=True,width_request=32,height_request=32)
            island.append(spinner);self.toasts.set_child(island)
        return GLib.SOURCE_REMOVE

    def _layout_changed(self,compact):
        compact=compact or self.handheld
        if compact!=self.compact:
            self.compact=compact
            if self.shape=='account':self.shape=None;self._update()

    def _say(self,message):
        if message and not self.closed:
            toast=Adw.Toast(title=message,timeout=5)
            self.toasts.add_toast(toast)

    # ── The Connect daemon ───────────────────────────────────────────────
    def _connected(self,_source,result):
        if self.closed:return
        try:
            self.proxy=Gio.DBusProxy.new_for_bus_finish(result)
            self.proxy.connect('g-signal',lambda *_:self._refresh())
            self.proxy.connect('notify::g-name-owner',self._owner_changed)
            self._refresh()
        except GLib.Error:self._unavailable()
    def _unavailable(self):
        state=dict(self.state or {'account_status':'signed_out'})
        state.update(stale=True,service_status='unavailable',login_available=False)
        self.state=state;self._update()
    def _owner_changed(self,*_):
        if not self.proxy.get_name_owner():self._unavailable()
        else:self._refresh()
    def _refresh(self):
        if not self.proxy or self.closed:return
        self.request_serial=getattr(self,'request_serial',0)+1;serial=self.request_serial
        def received(proxy,result):
            if self.closed or serial!=self.request_serial:return
            try:self.state=json.loads(proxy.call_finish(result).unpack()[0])
            except (GLib.Error,ValueError):self._unavailable();return
            self._update();self._companion_prompts()
        self.proxy.call('GetState',None,Gio.DBusCallFlags.NONE,5000,None,received)
    def _invoke(self,method,signature,values):
        if not self.proxy or self.closed:return
        def done(proxy,result):
            if self.closed:return
            try:proxy.call_finish(result)
            except GLib.Error:
                self._say('That didn’t finish. Try again in a moment.')
                return
            self._refresh()
        self.proxy.call(method,GLib.Variant(signature,values),Gio.DBusCallFlags.NONE,5000,None,done)

    # ── Luma Connect ───────────────────────────────────────────────────────
    def _cloud_tick(self):
        if self.closed:return GLib.SOURCE_REMOVE
        if self.is_visible() and not self.cloud_busy:self._refresh_cloud()
        return GLib.SOURCE_CONTINUE
    def _refresh_cloud(self):
        if self.closed or self.cloud_loading:return
        if not self.cloud_sync.available:
            self.cloud={'signed_in':False,'services':[],'devices':[]};self.cloud_problem='';self._update();return
        self.cloud_loading=True
        def received(report,problem):
            self.cloud_loading=False
            if self.closed:return
            if report is not None:self.cloud=report
            elif self.cloud is None:self.cloud={'signed_in':False,'services':[],'devices':[]}
            self.cloud_problem=problem or ((report or {}).get('problem') or '')
            self._update()
        self.cloud_sync.status(received)
    def _cloud_connected(self):
        return bool(self.cloud and self.cloud.get('signed_in'))
    def _this_device(self):
        return (self.cloud or {}).get('device') or {}
    def _cloud_devices(self):
        return [item for item in (self.cloud or {}).get('devices',[]) if not item.get('revoked')]
    def _last_sync(self):
        values=[item.get('last_success_at') or 0 for item in (self.cloud or {}).get('services',[]) if item.get('enabled')]
        return max(values) if values and max(values) else None

    # ── What the window shows ────────────────────────────────────────────
    def _relay_signed_in(self):
        return (self.state or {}).get('account_status')=='signed_in'

    def _update(self):
        """Show what changed, and only that. Refreshes that change nothing visible do nothing."""
        if self.closed:return
        if self.cloud is None and self.state is None:return
        if self.cloud is None and self.cloud_sync.available and not self._relay_signed_in():return  # still finding out
        if self._cloud_connected() or self._relay_signed_in():shape='account'
        elif self.phones_only:shape='phones-only'
        else:shape='welcome'
        if shape!=self.shape:
            self.shape=shape;self.page_key=None
            {'account':self._build_account_shell,'phones-only':self._build_phones_only,'welcome':self._build_welcome}[shape]()
            return
        if shape=='account':
            self._sync_sidebar();self._render_page()
            if getattr(self,'relay_refresh',None):self.relay_refresh()
        elif shape=='phones-only':
            self._render_page()

    def _render(self):
        """Kept for callers and fixtures that set state directly."""
        self._update()

    # The signed-out welcome ───────────────────────────────────────────────
    def _build_welcome(self):
        self.title_bar.set_visible(not self.handheld)
        island=Island()
        center=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10,valign=Gtk.Align.CENTER,vexpand=True,margin_start=28,margin_end=28,margin_top=24,margin_bottom=24)
        mark=Gtk.Image(icon_name=APP_ID,pixel_size=96,margin_bottom=12);center.append(mark)
        title=Gtk.Label(label='Luma Account',justify=Gtk.Justification.CENTER);title.add_css_class('luma-connect-welcome-title');center.append(title)
        lede=Gtk.Label(label='Your photos, contacts, calendar and notes, kept in step on every Luma device.',wrap=True,justify=Gtk.Justification.CENTER)
        lede.add_css_class('luma-connect-welcome-lede');lede.add_css_class('dim-label');center.append(lede)
        buttons=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8,halign=Gtk.Align.CENTER,margin_top=18)
        sign_in=Gtk.Button(label='Sign In…');sign_in.add_css_class('suggested-action');sign_in.add_css_class('pill')
        sign_in.set_size_request(220,-1);sign_in.connect('clicked',lambda *_:self._sign_in_dialog());buttons.append(sign_in)
        phones=Gtk.Button(label='Use a Phone Without an Account');phones.add_css_class('flat')
        phones.connect('clicked',lambda *_:(setattr(self,'phones_only',True),self._update()));buttons.append(phones)
        center.append(buttons)
        if not self.cloud_sync.available:
            note=Gtk.Label(label='Luma Connect sync isn’t installed on this computer.',wrap=True,justify=Gtk.Justification.CENTER)
            note.add_css_class('dim-label');note.add_css_class('caption');center.append(note);sign_in.set_sensitive(False)
        optional=Gtk.Label(label=OPTIONAL,wrap=True,justify=Gtk.Justification.CENTER,margin_top=26)
        optional.add_css_class('dim-label');optional.add_css_class('caption');center.append(optional)
        clamp=Adw.Clamp(maximum_size=420,tightening_threshold=320,child=center,vexpand=True)
        island.append(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,child=clamp,vexpand=True))
        self.toasts.set_child(island)
        sign_in.grab_focus()

    def _sign_in_dialog(self):
        # The shared layer host owns modality, dismissal and focus containment;
        # the sync engine still owns code acceptance and account persistence.
        host=LayerHost.window_host(self)
        content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14)
        card=Card(Adw.Clamp(child=content,maximum_size=440,tightening_threshold=440,unit=Adw.LengthUnit.PX))
        card.set_margin_start(16);card.set_margin_end(16)
        card.update_property([Gtk.AccessibleProperty.LABEL],['Sign In to Luma'])
        heading=TypeLabel('Sign In to Luma',role='title-1');content.append(heading)
        pages=Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        content.append(pages)
        first=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12,margin_start=2,margin_end=2,margin_top=2,margin_bottom=2)
        for text in ('Already use Luma on another device? Open Luma Connect there, choose Devices, then Add a Device. It shows a one-time code.',
                     'New to Luma? Create your account on Luma Hub, then choose Connect a Device to get a code.'):
            first.append(TypeLabel(text,wrap=True))
        hub=TextButton('Open Luma Hub',icon='arrow-up-right',style='raised',on_click=lambda:_launch(HUB_CONNECT_PAGE,self))
        have=TextButton('Enter a Code…',icon='key-round',style='key')
        first.append(hub);first.append(have)
        cancel=TextButton('Cancel',on_click=lambda:handle.cancel());cancel.set_halign(Gtk.Align.END);first.append(cancel)
        pages.add_named(first,'how')
        second=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12,margin_start=2,margin_end=2,margin_top=2,margin_bottom=2)
        second.append(TypeLabel('The code has 8 letters and numbers and works once.',wrap=True))
        code=TextField('Code');name=TextField('Name for this computer',value=device_name(),purpose='name')
        second.append(code);second.append(name)
        status=TypeLabel('',wrap=True);status.set_visible(False);second.append(status)
        actions=Gtk.Box(spacing=8,halign=Gtk.Align.END)
        back=TextButton('Back',icon='chevron-left',style='raised')
        connect=TextButton('Sign In',style='key',sensitive=False)
        actions.append(back);actions.append(connect);second.append(actions)
        pages.add_named(second,'code')
        busy=False
        def update(*_):
            connect.set_sensitive(not busy and len(''.join(code.entry.get_text().split()))==8)
        code.entry.connect('changed',update)
        def submit(*_):
            nonlocal busy
            value=''.join(code.entry.get_text().split())
            if handle.closed or busy or len(value)!=8:return
            busy=True;update();code.set_sensitive(False);name.set_sensitive(False);back.set_sensitive(False)
            status.set_text('Signing in…');status.set_visible(True)
            def done(problem):
                nonlocal busy
                if handle.closed:return
                busy=False
                if problem:
                    code.set_sensitive(True);name.set_sensitive(True);back.set_sensitive(True);update()
                    status.set_text(problem);code.entry.grab_focus();return
                handle.close();self._say('Signed in.');self._refresh_cloud()
            self.cloud_sync.connect(value,name.entry.get_text().strip(),done)
        connect.connect('clicked',submit);code.entry.connect('activate',submit)
        def show_code(*_):
            heading.set_text('Enter Your Code');pages.set_visible_child_name('code');code.entry.grab_focus()
        def show_help(*_):
            heading.set_text('Sign In to Luma');pages.set_visible_child_name('how');have.grab_focus()
        have.connect('clicked',show_code);back.connect('clicked',show_help)
        handle=host.present_modal(card,initial_focus=have)
        return handle

    # Phones without an account ────────────────────────────────────────────
    def _build_phones_only(self):
        self.title_bar.set_visible(not self.handheld)
        island=Island();self.content_bin=Adw.Bin(vexpand=True);island.append(self.content_bin)
        self.toasts.set_child(island);self._render_page()

    # The account ─────────────────────────────────────────────────────────
    def _build_account_shell(self):
        self.title_bar.set_visible(not self.handheld)
        self.sidebar=NavigationSidebar();self.rows={}
        # The sidebar island and the content island sit apart, as in every Luma app.
        self.sidebar.set_margin_end(9)
        for key,label,icon in PANELS:
            if key=='account':
                portrait=Avatar(self._account_name(),compact=True) if Avatar else None
                row=NavigationRow(self._account_name(),icon_widget=portrait,icon_name=None if portrait else icon,subtitle='Luma Account')
                row.add_css_class('luma-connect-sidebar-account')
            else:
                row=NavigationRow(label,icon_name=icon)
            row.panel=key;self.sidebar.append_row(row);self.rows[key]=row
        self.sidebar_status=Gtk.Label(xalign=0,wrap=True,margin_start=14,margin_end=12,margin_bottom=12,margin_top=6)
        self.sidebar_status.add_css_class('caption');self.sidebar_status.add_css_class('dim-label')
        self.sidebar.append_footer(self.sidebar_status)
        self.content_bin=Adw.Bin(vexpand=True,hexpand=True)
        content=Island();content.append(self.content_bin)
        self.split=IslandSplitView(collapsed=self.compact,show_content=not self.compact)
        self.split.set_sidebar_width_unit(Adw.LengthUnit.PX)
        self.split.set_min_sidebar_width(187);self.split.set_max_sidebar_width(187)
        self.split.set_sidebar(Adw.NavigationPage.new(self.sidebar,'Luma Connect'))
        self.split.set_content(Adw.NavigationPage.new(content,'Account'))
        self.toasts.set_child(self.split)
        self.sidebar.list.connect('row-selected',self._selected)
        self.sidebar.list.connect('row-activated',lambda _l,_r:self.compact and self.split.set_show_content(True))
        self._sync_sidebar()
        self.sidebar.list.select_row(self.rows[self.panel])

    def _account_name(self):
        cloud=(self.cloud or {}).get('account') or {}
        profile=(self.state or {}).get('profile') or {}
        return cloud.get('name') or profile.get('name') or 'Luma Account'

    def _sync_sidebar(self):
        if self._cloud_connected():
            last=self._last_sync()
            text=when_text(last,prefix='Synced ') if last else 'Waiting for the first sync'
            if not (self.cloud or {}).get('reachable',True):text='Offline · '+text.lower() if last else 'Offline'
        elif self._relay_signed_in():text='Luma Connect isn’t connected'
        else:text=''
        if self.sidebar_status.get_label()!=text:self.sidebar_status.set_label(text)
        self.sidebar_status.set_visible(bool(text))

    def select_panel(self,panel):
        row=self.rows.get(panel)
        if row is not None:self.sidebar.list.select_row(row)
        if self.compact and hasattr(self,'split'):self.split.set_show_content(True)

    def _selected(self,_list,row):
        if row is None:return
        self.panel=row.panel;self.remember('panel',row.panel);self.page_key=None;self._render_page()

    def _render_page(self):
        panel='phones' if self.shape=='phones-only' else self.panel
        key=json.dumps([panel,self.compact,stable(self.state),stable(self.cloud),sorted(self.cloud_busy),self.syncing_now,self.cloud_problem],sort_keys=True,default=str)
        if key==self.page_key:return
        scroll=None
        current=self.content_bin.get_child()
        if isinstance(current,Page) and getattr(current,'panel',None)==panel:scroll=current.get_vadjustment().get_value()
        self.page_key=key
        page={'account':self._account_page,'sync':self._sync_page,'devices':self._devices_page,'phones':self._phones_page}[panel]()
        page.panel=panel
        self.content_bin.set_child(page)
        if scroll:GLib.idle_add(lambda:(page.get_vadjustment().set_value(scroll),False)[1])

    def _back(self):
        if not self.compact:return None
        button=Gtk.Button(icon_name='luma-chevron-left-symbolic',valign=Gtk.Align.CENTER,tooltip_text='Back')
        button.add_css_class('flat');button.connect('clicked',lambda *_:self.split.set_show_content(False))
        return button

    # Rows ─────────────────────────────────────────────────────────────────
    def _row(self,group,title,subtitle,icon=None):
        row=Adw.ActionRow(title=title,subtitle=subtitle,use_markup=False)
        if icon:row.add_prefix(Gtk.Image(icon_name=icon))
        group.add(row);return row
    def _value_row(self,group,title,value):
        row=Adw.ActionRow(title=title,use_markup=False)
        label=Gtk.Label(label=value,ellipsize=Pango.EllipsizeMode.END,selectable=True);label.add_css_class('dim-label')
        row.add_suffix(label);group.add(row);return row
    def _link_row(self,group,title,subtitle,icon,callback,*,external=False):
        row=Adw.ActionRow(title=title,subtitle=subtitle,use_markup=False,activatable=True)
        if icon:row.add_prefix(Gtk.Image(icon_name=icon))
        row.add_suffix(Gtk.Image(icon_name='luma-connect-external-symbolic' if external else 'luma-connect-chevron-symbolic',css_classes=['dim-label']))
        row.connect('activated',lambda *_:callback());group.add(row);return row
    def _empty(self,group,title,description,icon):
        group.add(EmptyState(title,description,icon,compact=True))
    def _suffix_button(self,label,callback,*,sensitive=True,style=None):
        button=Gtk.Button(label=label,valign=Gtk.Align.CENTER,sensitive=sensitive)
        if style:button.add_css_class(style)
        button.connect('clicked',lambda *_:callback());return button
    def _app_icon(self,app_id,fallback,size=32):
        display=Gdk.Display.get_default()
        theme=Gtk.IconTheme.get_for_display(display) if display else None
        if app_id and theme is not None and theme.has_icon(app_id):
            return Gtk.Image(icon_name=app_id,pixel_size=size,valign=Gtk.Align.CENTER)
        return tile(fallback)

    # Account ──────────────────────────────────────────────────────────────
    def _account_page(self):
        page=Page('',back=None)
        name=self._account_name()
        hero=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6,halign=Gtk.Align.CENTER,margin_top=8)
        if self.compact:
            back=self._back()
            if back:back.set_halign(Gtk.Align.START);page.add(back)
        if Avatar:hero.append(Avatar(name,hero=True))
        title=Gtk.Label(label=name,wrap=True,justify=Gtk.Justification.CENTER,margin_top=8);title.add_css_class('luma-connect-hero-name');hero.append(title)
        hub=((self.cloud or {}).get('hub') or DEFAULT_HUB)
        profile=(self.state or {}).get('profile') or {}
        detail=('Luma Account · '+hub.removeprefix('https://')) if self._cloud_connected() else (profile.get('email') or 'Luma Account')
        caption=Gtk.Label(label=detail,wrap=True,justify=Gtk.Justification.CENTER,selectable=True)
        caption.add_css_class('dim-label');caption.add_css_class('luma-connect-hero-detail');hero.append(caption)
        page.add(hero)

        if self._cloud_connected():
            self._profile_groups(page)
        else:
            about=Adw.PreferencesGroup(title='Luma Connect');page.add(about)
            row=self._row(about,'Not connected',self.cloud_problem or 'Connect this computer to keep your calendar, notes, contacts and photos in step.')
            row.add_suffix(self._suffix_button('Sign In…',self._sign_in_dialog,style='suggested-action'))

        overview=Adw.PreferencesGroup();page.add(overview)
        services=[item for item in (self.cloud or {}).get('services',[])]
        on=sum(1 for item in services if item.get('enabled'))
        if self._cloud_connected():
            self._link_row(overview,'Syncing',f'{on} of {len(services)} on' if services else 'Nothing to sync yet','luma-connect-sync-symbolic',lambda:self.select_panel('sync'))
            count=len(self._cloud_devices())
            self._link_row(overview,'Devices','1 device' if count==1 else f'{count} devices','luma-connect-laptop-symbolic',lambda:self.select_panel('devices'))
        phones=(self.state or {}).get('companion_devices') or []
        self._link_row(overview,'Phones',', '.join(p.get('name') or 'Phone' for p in phones[:2]) or 'None paired','luma-connect-phone-symbolic',lambda:self.select_panel('phones'))

        signout=Gtk.Button(label='Sign Out…',halign=Gtk.Align.CENTER,margin_top=6)
        signout.add_css_class('luma-connect-signout');signout.add_css_class('destructive-action')
        signout.connect('clicked',lambda *_:self._confirm_sign_out_here())
        page.add(signout)
        return page

    # The profile the hub keeps: name, email, phone number, whether people may
    # find the account by that number, and where its password lives.
    def _profile_groups(self,page):
        profile=(self.cloud or {}).get('profile')
        about=Adw.PreferencesGroup(title='Account');page.add(about)
        if not isinstance(profile,dict):
            reachable=(self.cloud or {}).get('reachable',True)
            self._row(about,'Name, Email and Phone Number',self.cloud_problem or ('Update Luma to see and change them here.' if reachable
                else 'Connect to the internet to see and change them.'))
            self._link_row(about,'Luma Hub','Your devices and synced things, on the web.',None,lambda:_launch(HUB_CONNECT_PAGE,self),external=True)
            return
        texts=profile_subtitles(profile)
        for field,title in (('name','Name'),('email','Email'),('phone','Phone Number')):
            if 'profile:'+field in self.cloud_busy:
                row=self._row(about,title,'Saving…');row.add_suffix(Adw.Spinner(valign=Gtk.Align.CENTER))
            else:
                self._link_row(about,title,texts[field],None,lambda field=field:self._profile_dialog(field))
        find=Adw.PreferencesGroup(title='Finding You');page.add(find)
        if 'profile:discoverable_by_phone' in self.cloud_busy:
            row=self._row(find,'Let People Find You by Phone Number','Saving…');row.add_suffix(Adw.Spinner(valign=Gtk.Align.CENTER))
        else:
            switch=Adw.SwitchRow(title='Let People Find You by Phone Number',subtitle=discoverable_subtitle(profile),
                use_markup=False,active=bool(profile.get('discoverable_by_phone')))
            switch.set_sensitive(bool(profile.get('phone')) and not self.cloud_busy)
            switch.connect('notify::active',self._discoverable_toggled,profile)
            find.add(switch)
        signin=Adw.PreferencesGroup(title='Sign-In');page.add(signin)
        manage=(profile.get('sign_in') or {}).get('manage_url')
        if manage:
            self._link_row(signin,'Sign-In Method',sign_in_subtitle(profile),None,lambda:_launch(manage,self),external=True)
        else:
            self._row(signin,'Sign-In Method',sign_in_subtitle(profile))
        self._link_row(signin,'Luma Hub','Your devices and synced things, on the web.',None,lambda:_launch(HUB_CONNECT_PAGE,self),external=True)

    def _profile_dialog(self,field,typed=None,problem=''):
        profile=(self.cloud or {}).get('profile') or {}
        provider=provider_name(profile)
        current={'name':profile.get('name') or '','email':profile.get('email') or '',
                 'phone':connect_format_phone(profile.get('phone'))}[field]
        empty=not current
        heading={'name':'Change Your Name','email':'Add an Email Address' if empty else 'Change Your Email Address',
                 'phone':'Add a Phone Number' if empty else 'Change Your Phone Number'}[field]
        body={'name':'This is the name people see on Luma Hub and on your devices.',
              'email':('Luma can’t send verification email yet, so an address you enter here shows as not verified.'
                       +(f' The address from {provider} is verified by {provider}.' if profile.get('sign_in_email') else '')),
              'phone':'Include your country code, like +1 for the US. Luma can’t send text messages yet, so your number shows as not verified.'}[field]
        dialog=Adw.AlertDialog(heading=heading,body=body)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        rows=Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE);rows.add_css_class('boxed-list')
        entry=Adw.EntryRow(title={'name':'Name','email':'Email','phone':'Phone number'}[field],text=current if typed is None else typed,activates_default=True)
        entry.set_input_purpose({'name':Gtk.InputPurpose.NAME,'email':Gtk.InputPurpose.EMAIL,'phone':Gtk.InputPurpose.PHONE}[field])
        rows.append(entry);box.append(rows)
        if problem:
            error=Gtk.Label(label=problem,wrap=True,xalign=0);error.add_css_class('error');error.add_css_class('caption')
            error.update_property([Gtk.AccessibleProperty.LABEL],[problem]);box.append(error)
        dialog.set_extra_child(box)
        dialog.add_response('cancel','Cancel')
        reset=None
        if field=='name' and profile.get('name_source')=='profile':reset=('reset',f'Use {provider} Name',None)
        if field=='email' and profile.get('email_source')=='profile' and profile.get('sign_in_email'):reset=('reset',f'Use {provider} Email',None)
        if field=='phone' and not empty:reset=('reset','Remove',Adw.ResponseAppearance.DESTRUCTIVE)
        if reset:
            dialog.add_response(reset[0],reset[1])
            if reset[2]:dialog.set_response_appearance(reset[0],reset[2])
        dialog.add_response('save','Save');dialog.set_response_appearance('save',Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('save');dialog.set_close_response('cancel')
        def changed(*_):
            text=entry.get_text().strip()
            # A name cannot be blank; a blank email or number means remove it.
            dialog.set_response_enabled('save',text!=current and (bool(text) or field!='name'))
        entry.connect('changed',changed);changed()
        def answered(_dialog,response):
            if response=='cancel':return
            text=entry.get_text().strip()
            value=None if response=='reset' or not text else text
            done={'name':'Name changed.' if value else f'Now using your {provider} name.',
                  'email':'Email address saved. It isn’t verified.' if value else ('Now using your '+provider+' email.' if profile.get('sign_in_email') else 'Email address removed.'),
                  'phone':'Phone number saved. It isn’t verified.' if value else 'Phone number removed.'}[field]
            # A refusal reopens the dialog with what was typed and the hub's reason.
            self._save_profile({field:value},done,problem=lambda message:self._profile_dialog(field,text,message))
        dialog.connect('response',answered);dialog.present(self)
        entry.grab_focus()

    def _discoverable_toggled(self,switch,_pspec,profile):
        wanted=switch.get_active()
        if wanted==bool(profile.get('discoverable_by_phone')) or self.cloud_busy:return
        def put_back():
            switch.handler_block_by_func(self._discoverable_toggled);switch.set_active(not wanted);switch.handler_unblock_by_func(self._discoverable_toggled)
        if not wanted:
            self._save_profile({'discoverable_by_phone':False},'People can’t find you by your phone number.')
            return
        dialog=Adw.AlertDialog(heading='Let people find you by phone number?',
            body=('Once your number is verified, people who already have it can find your Luma account in Messages. '
                  'Luma can’t verify phone numbers yet, so nobody can find you by it today. You can turn this off at any time.'))
        dialog.add_response('cancel','Cancel');dialog.add_response('on','Turn On')
        dialog.set_response_appearance('on',Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('on');dialog.set_close_response('cancel')
        def answered(_dialog,response):
            if response!='on':put_back();return
            self._save_profile({'discoverable_by_phone':True},'Turned on. It starts once your number is verified.')
        dialog.connect('response',answered);dialog.present(self)

    def _save_profile(self,changes,done,*,problem=None):
        keys=['profile:'+key for key in changes]
        self.cloud_busy.update(keys);self._render_page()
        def finished(profile,refusal):
            self.cloud_busy.difference_update(keys)
            if self.closed:return
            if refusal or profile is None:
                reason=refusal or 'That wasn’t saved. Try again.'
                self._render_page()  # a switch goes back to what the hub still has
                if problem:problem(reason)
                else:self._say(f'That wasn’t saved: {reason}')
                return
            cloud=dict(self.cloud or {});cloud['profile']=profile
            cloud['account']={**(cloud.get('account') or {}),'name':profile.get('name') or ''}
            self.cloud=cloud;self._say(done)
            if 'name' in changes and getattr(self,'rows',None) and 'account' in self.rows:
                self.shape=None  # the sidebar carries the name and portrait
            self._update()
        self.cloud_sync.set_profile(changes,finished)

    def _confirm_sign_out_here(self):
        dialog=Adw.AlertDialog(heading='Sign out of Luma on this computer?',
            body=('This computer stops syncing, and phone messages and calls stop coming here. '
                  'Everything on it stays, and your Luma calendar events are copied into Personal first. '
                  'Your other devices keep everything too.'))
        dialog.add_response('cancel','Cancel');dialog.add_response('signout','Sign Out')
        dialog.set_response_appearance('signout',Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel');dialog.set_close_response('cancel')
        def answered(_dialog,response):
            if response!='signout':return
            if self._relay_signed_in():self._invoke('SignOut','(s)',('current',))
            if self._cloud_connected():
                def done(problem,_out):
                    self._say(f'Sign-out didn’t finish: {problem}' if problem else 'Signed out of Luma on this computer.')
                    self._refresh_cloud()
                self.cloud_sync.sign_out('',done)
        dialog.connect('response',answered);dialog.present(self)

    # Syncing ──────────────────────────────────────────────────────────────
    def _sync_page(self):
        refresh=None
        if self._cloud_connected():
            refresh=Gtk.Button(valign=Gtk.Align.CENTER,sensitive=not self.syncing_now)
            content=Gtk.Box(spacing=6)
            content.append(Adw.Spinner() if self.syncing_now else Gtk.Image(icon_name='luma-connect-sync-symbolic'))
            content.append(Gtk.Label(label='Syncing…' if self.syncing_now else 'Sync Now'));refresh.set_child(content)
            refresh.connect('clicked',lambda *_:self._sync_now())
        page=Page('Syncing','Choose what this computer keeps in step with your other Luma devices. Turning something off never deletes it, here or anywhere else.',
            suffix=refresh,back=self._back())
        if not self._cloud_connected():
            group=Adw.PreferencesGroup();page.add(group)
            row=self._row(group,'Luma Connect isn’t connected',self.cloud_problem or 'Sign in to keep your calendar, notes, contacts and photos in step.')
            row.add_suffix(self._suffix_button('Sign In…',self._sign_in_dialog,style='suggested-action'))
        else:
            if self.cloud_problem:
                notice=Adw.PreferencesGroup();page.add(notice)
                self._row(notice,'Needs attention',self.cloud_problem,'dialog-warning-symbolic')
            group=Adw.PreferencesGroup();page.add(group)
            for item in self.cloud.get('services',[]):
                row=Adw.ActionRow(title=item['name'],subtitle=service_subtitle(item),use_markup=False)
                row.add_prefix(self._app_icon(SERVICE_APPS.get(item['id'],''),'luma-connect-cloud-symbolic'))
                if item['id'] in self.cloud_busy:
                    row.set_subtitle('Updating…');row.add_suffix(Adw.Spinner(valign=Gtk.Align.CENTER))
                else:
                    switch=Gtk.Switch(active=bool(item.get('enabled')),valign=Gtk.Align.CENTER)
                    switch.update_property([Gtk.AccessibleProperty.LABEL],[item['name']])
                    switch.connect('notify::active',self._service_toggled,item)
                    row.add_suffix(switch);row.set_activatable_widget(switch)
                group.add(row)
        # Settings the relay account carries, when it has any this computer can use.
        state=self.state or {}
        items=[item for item in ((state.get('sync') or {}).get('items') or []) if item.get('visible',True) and item['id']!='clipboard' and item.get('available')]
        if items and self._relay_signed_in():
            ready=not state.get('stale') and not state.get('busy')
            other=Adw.PreferencesGroup(title='Relay',description='Turning these off keeps what is already copied. It stops further copying.');page.add(other)
            for item in items:
                row=Adw.SwitchRow(title=item.get('label',item['id']),use_markup=False,active=bool(item.get('enabled')));row.set_sensitive(ready)
                row.connect('notify::active',lambda widget,_pspec,key=item['id']:self._invoke('SetSyncEnabled','(sb)',(key,widget.get_active())))
                other.add(row)
        return page

    def _sync_now(self):
        self.syncing_now=True;self._render_page()
        def done(problem):
            self.syncing_now=False
            self._say(f'Sync didn’t finish: {problem}' if problem else 'Everything is in step.')
            self._refresh_cloud();self._render_page()
        self.cloud_sync.sync_now(done)

    def _service_toggled(self,switch,_pspec,item):
        wanted=switch.get_active()
        if wanted==bool(item.get('enabled')) or item['id'] in self.cloud_busy:return
        dialog=Adw.AlertDialog(heading=('Turn on ' if wanted else 'Turn off ')+item['name']+'?',body=item.get('on' if wanted else 'off') or '')
        dialog.add_response('cancel','Cancel');dialog.add_response('go','Turn On' if wanted else 'Turn Off')
        dialog.set_response_appearance('go',Adw.ResponseAppearance.SUGGESTED if wanted else Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel' if not wanted else 'go');dialog.set_close_response('cancel')
        def answered(_dialog,response):
            if response!='go':
                switch.handler_block_by_func(self._service_toggled);switch.set_active(bool(item.get('enabled')));switch.handler_unblock_by_func(self._service_toggled);return
            self.cloud_busy.add(item['id']);self._render_page()
            def done(problem):
                self.cloud_busy.discard(item['id'])
                if problem:self._say(f"{item['name']} wasn’t changed: {problem}")
                self._refresh_cloud();self._render_page()
            self.cloud_sync.set_service(item['id'],wanted,done)
        dialog.connect('response',answered);dialog.present(self)

    # Devices ──────────────────────────────────────────────────────────────
    def _devices_page(self):
        page=Page('Devices','Every device signed in to your Luma account.',back=self._back())
        if not self._cloud_connected():
            group=Adw.PreferencesGroup();page.add(group)
            row=self._row(group,'Luma Connect isn’t connected',self.cloud_problem or 'Sign in, and the devices on your account show up here.')
            row.add_suffix(self._suffix_button('Sign In…',self._sign_in_dialog,style='suggested-action'))
            return page
        this=self._this_device().get('id')
        devices=sorted(self._cloud_devices(),key=lambda item:(item.get('id')!=this,(item.get('name') or '').casefold()))
        here=Adw.PreferencesGroup(title='This Computer');page.add(here)
        others=Adw.PreferencesGroup(title='Other Devices')
        for item in devices:
            last=max([service.get('last_success_at') or 0 for service in item.get('services',[])] or [0])
            mine=item.get('id')==this
            if mine and not last:last=self._last_sync() or 0
            name=item.get('name') or 'Device'
            row=Adw.ActionRow(title=name,subtitle=when_text(last,prefix='Synced ') if last else 'Not synced yet',use_markup=False)
            row.add_prefix(tile(device_icon(name)))
            if mine:
                here.add(row)
            else:
                more=Gtk.MenuButton(icon_name='luma-connect-more-symbolic',valign=Gtk.Align.CENTER,tooltip_text=f'More for {name}')
                more.add_css_class('flat')
                menu=Gio.Menu();menu.append('Remove from Account…','win.remove-device::'+item['id']);more.set_menu_model(menu)
                row.add_suffix(more);others.add(row)
        self._ensure_action('remove-device',lambda target:self._confirm_cloud_signout(target,next(
            (d.get('name') or 'this device' for d in self._cloud_devices() if d.get('id')==target),'this device')))
        if others.get_first_child() is not None and any(item.get('id')!=this for item in devices):page.add(others)
        signed_out=[item for item in (self.cloud or {}).get('devices',[]) if item.get('revoked')]
        add=Adw.PreferencesGroup();page.add(add)
        self._link_row(add,'Add a Device','Get a one-time code for a phone or another computer.','luma-connect-plus-symbolic',self._invite_dialog)
        if signed_out:
            expander=Adw.ExpanderRow(title='Removed Devices',subtitle=f'{len(signed_out)} no longer syncing',use_markup=False)
            for item in signed_out:
                expander.add_row(Adw.ActionRow(title=item.get('name') or 'Device',subtitle='Removed',use_markup=False))
            add.add(expander)
        return page

    def _invite_dialog(self):
        dialog=Adw.Dialog(title='Add a Device',content_width=400,follows_content_size=True)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14,margin_start=24,margin_end=24,margin_top=18,margin_bottom=24)
        steps=Gtk.Label(label='On the other device, open Luma Connect, choose Sign In, then Enter a Code.',wrap=True,justify=Gtk.Justification.CENTER)
        box.append(steps)
        code=Gtk.Label(label='',selectable=True,halign=Gtk.Align.CENTER);code.add_css_class('luma-connect-code')
        spinner=Adw.Spinner(halign=Gtk.Align.CENTER,width_request=28,height_request=28)
        box.append(spinner);box.append(code)
        note=Gtk.Label(label='Getting a code…',wrap=True,justify=Gtk.Justification.CENTER);note.add_css_class('dim-label');note.add_css_class('caption')
        box.append(note)
        toolbar=Adw.ToolbarView();toolbar.add_top_bar(Adw.HeaderBar());toolbar.set_content(box);dialog.set_child(toolbar)
        def got(value,problem):
            spinner.set_visible(False)
            if value:
                code.set_label(f'{value[:4]} {value[4:]}' if len(value)==8 else value)
                code.update_property([Gtk.AccessibleProperty.LABEL],['Code '+' '.join(value)])
                note.set_label('It works once, for the next ten minutes.')
            else:
                note.set_label(problem or 'A code couldn’t be made. Check the connection and try again.')
        self.cloud_sync.invite(got)
        dialog.present(self)

    def _confirm_cloud_signout(self,target,name):
        dialog=Adw.AlertDialog(heading=f'Remove {name} from your account?',
            body=f'{name} stops syncing with Luma Connect. Nothing on it is deleted, and it can join again with a new code.')
        dialog.add_response('cancel','Cancel');dialog.add_response('signout','Remove')
        dialog.set_response_appearance('signout',Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel');dialog.set_close_response('cancel')
        def answered(_dialog,response):
            if response!='signout':return
            def done(problem,_out):
                self._say(f'{name} wasn’t removed: {problem}' if problem else f'{name} was removed.')
                self._refresh_cloud()
            self.cloud_sync.sign_out(target,done)
        dialog.connect('response',answered);dialog.present(self)

    # Phones ───────────────────────────────────────────────────────────────
    def _phones_page(self):
        state=self.state or {}
        pair=None
        if state.get('enabled') and (state.get('companion_devices') or []):
            pair=Gtk.Button(valign=Gtk.Align.CENTER)
            content=Gtk.Box(spacing=6);content.append(Gtk.Image(icon_name='luma-connect-plus-symbolic'));content.append(Gtk.Label(label='Pair a Phone…'))
            pair.set_child(content);pair.connect('clicked',lambda *_:self._pair_phone())
        back=self._back()
        if self.shape=='phones-only':
            back=Gtk.Button(icon_name='luma-chevron-left-symbolic',valign=Gtk.Align.CENTER,tooltip_text='Back to sign-in')
            back.add_css_class('flat');back.connect('clicked',lambda *_:(setattr(self,'phones_only',False),self._update()))
        page=Page('Phones','Your phone’s notifications and texts on this computer, copied text and files back and forth, and calls.',suffix=pair,back=back)
        self._phones(page,self.compact)
        if self._relay_signed_in() or (state.get('message_devices') or state.get('call_devices')):
            self._relay_section(page)
        elif state.get('account_status')=='locked':
            self._relay_section(page)
        return page

    def _relay_section(self,page):
        """Phones on the Luma account reached through the Connect relay."""
        state=self.state or {}
        ready=not state.get('stale') and not state.get('busy')
        group=Adw.PreferencesGroup(title='Messages and Calls Relay',
            description='Phones on your Luma account can share texts and calls with this computer from anywhere.');page.add(group)
        status=state.get('account_status')
        if status=='locked':
            row=self._row(group,'Your keyring is locked','Unlock it to reconnect the relay.','luma-connect-lock-symbolic')
            row.add_suffix(self._suffix_button('Unlock…',lambda:self._invoke('UnlockAccount','()',()),sensitive=not state.get('busy'),style='suggested-action'))
            return
        if state.get('service_status')=='unavailable' or state.get('stale'):
            self._row(group,'Relay isn’t responding','Showing what was last known. It reconnects by itself.','dialog-warning-symbolic')
        messages=state.get('message_devices') or [];calls=state.get('call_devices') or []
        peers={item['peer'] for item in messages}|{item['peer'] for item in calls}
        parts=[]
        if messages:parts.append('texts shared' if any(i.get('sharing') or i.get('can_read') for i in messages) else 'texts need a choice on the phone')
        if calls:parts.append('calls connected' if any(i.get('connected') for i in calls) else 'calls not connected')
        summary=(('1 phone' if len(peers)==1 else f'{len(peers)} phones')+(' · '+', '.join(parts) if parts else '')) if peers else 'No phones on the relay yet'
        self._link_row(group,'Relay Settings',summary,'luma-connect-phone-symbolic',self._relay_dialog)

    def _relay_dialog(self):
        dialog=Adw.Dialog(title='Messages and Calls Relay',content_width=560,content_height=620)
        holder=Adw.Bin()
        shown=[None]
        def fill():
            state=self.state or {};ready=not state.get('stale') and not state.get('busy')
            key=json.dumps(stable(state),sort_keys=True,default=str)
            if key==shown[0]:return
            shown[0]=key
            page=Adw.PreferencesPage()
            profile=state.get('profile') or {}
            if profile:
                who=Adw.PreferencesGroup(title='Signed In');page.add(who)
                self._row(who,profile.get('name') or 'Relay account',profile.get('email') or '','luma-connect-user-symbolic')
            self._message_devices(page,ready)
            self._call_devices(page,ready)
            sessions=(state.get('sessions') or {}).get('items') or []
            others=[item for item in sessions if not item.get('current')]
            if others:
                group=Adw.PreferencesGroup(title='Computers Using the Relay');page.add(group)
                for item in others:
                    title=item.get('device_name') or 'Computer'
                    row=Adw.ActionRow(title=title,subtitle='Last active '+when_text(item.get('last_access_at')),use_markup=False)
                    if item.get('can_revoke_here'):
                        row.add_suffix(self._suffix_button('Sign Out',lambda target=item['session_id'],name=title:self._confirm_signout(target,name),sensitive=ready))
                    group.add(row)
            holder.set_child(page)
        fill()
        toolbar=Adw.ToolbarView();toolbar.add_top_bar(Adw.HeaderBar());toolbar.set_content(holder);dialog.set_child(toolbar)
        self.relay_refresh=fill
        dialog.connect('closed',lambda *_:setattr(self,'relay_refresh',None))
        dialog.present(self)

    def _message_devices(self, page, ready):
        group=Adw.PreferencesGroup(title='Phone messages')
        page.add(group)
        self._pairing_controls(group,ready)
        devices=self.state.get('message_devices') or []
        if not devices:
            self._row(group,'No paired phone','Pair a phone on your account and approve message access on it.','luma-connect-phone-symbolic')
        for item in devices:
            title='Paired phone '+item['peer'][:8]
            row=self._row(group,title,'Sharing conversations' if item.get('sharing') else 'Messages from this phone show in Messages' if item.get('can_read') else 'Message access needs a choice on the phone')
            actions=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6,valign=Gtk.Align.CENTER)
            if self.compact:
                for setter in (actions.set_margin_start,actions.set_margin_end,actions.set_margin_bottom):setter(12)
                holder=Gtk.ListBoxRow(activatable=False,selectable=False,child=actions);group.add(holder)
            else:row.add_suffix(actions)
            if item.get('can_share'):
                button=Gtk.Button(label='Stop Sharing' if item.get('sharing') else 'Share Conversations',valign=Gtk.Align.CENTER,sensitive=ready)
                button.connect('clicked',lambda _button,peer=item['peer'],active=item.get('sharing'):
                    self._invoke('StopMessageSharing','()',()) if active else self._message_endpoint(peer,True))
                actions.append(button)
            for incoming,eligible,active in ((False,item.get('can_read'),item.get('can_send')),
                                             (True,item.get('can_share'),item.get('can_receive_sends'))):
                if not eligible:continue
                title=('Stop Allowing Sends' if active else 'Allow Sending from This Device') if incoming else ('Stop Sending' if active else 'Send Through Phone')
                button=Gtk.Button(label=title,sensitive=ready)
                def sending(_button,peer=item['peer'],direction=incoming,enabled=not bool(active)):
                    if not enabled:
                        self._invoke('SetMessageSending','(sbb)',(peer,direction,False));return
                    dialog=Adw.AlertDialog(heading='Allow message sending?',body='This paired device can send SMS and picture messages through this phone. Carrier charges may apply.' if direction else 'Use this paired phone to send messages. The phone must also allow sending.')
                    dialog.add_response('cancel','Cancel');dialog.add_response('allow','Allow Sending');dialog.set_close_response('cancel')
                    dialog.connect('response',lambda _dialog,response:self._invoke('SetMessageSending','(sbb)',(peer,direction,True)) if response=='allow' else None)
                    dialog.present(self)
                button.connect('clicked',sending);actions.append(button)
            revoke=Gtk.Button(label='Revoke',valign=Gtk.Align.CENTER,sensitive=ready)
            def confirm(_button,peer=item['peer']):
                dialog=Adw.AlertDialog(heading='Revoke message access?',body='Stop access for this paired device. Conversations already downloaded stay on this computer.')
                dialog.add_response('cancel','Cancel');dialog.add_response('revoke','Revoke')
                dialog.set_response_appearance('revoke',Adw.ResponseAppearance.DESTRUCTIVE)
                dialog.set_close_response('cancel')
                dialog.connect('response',lambda _dialog,response:self._invoke('RevokeMessagePeer','(s)',(peer,)) if response=='revoke' else None)
                dialog.present(self)
            revoke.connect('clicked',confirm);actions.append(revoke)

    def _call_devices(self,page,ready):
        devices=self.state.get('call_devices') or []
        if not devices:return
        group=Adw.PreferencesGroup(title='Phone calls',description='Choose call access separately on both paired devices. Audio moves only when you choose it during a call.')
        page.add(group)
        for item in devices:
            incoming=item['role']=='receiver';peer=item['peer']
            from .call_retry import description
            self._row(group,'Paired phone '+peer[:8],'Connected' if item['connected'] else description(item.get('connection',{})))
            for capability,label in [('calls.read','Share call status' if incoming else 'Read phone call status'),
                                     ('calls.control','Allow call controls'),('calls.audio','Allow call audio')]:
                row=Adw.SwitchRow(title=label,use_markup=False)
                row.set_active(item['permissions'].get(capability,False));row.set_sensitive(ready)
                row.connect('notify::active',lambda widget,_pspec,peer=peer,capability=capability,incoming=incoming:
                    self._invoke('SetCallPermission','(ssbb)',(peer,capability,incoming,widget.get_active())))
                group.add(row)
            action=Gtk.Button(label=('Stop Sharing Calls' if item['sharing'] else 'Share Calls') if incoming else ('Retry Connection' if item.get('selected') else 'Use This Phone for Calls'),
                sensitive=ready and item['permissions'].get('calls.read',False),halign=Gtk.Align.START,margin_top=6)
            def selected(_button,peer=peer,incoming=incoming,sharing=item['sharing'],is_selected=item.get('selected',False)):
                if incoming:
                    self._invoke('StopCallSharing','()',()) if sharing else self._invoke('StartCallSharing','(ssu)',(peer,'127.0.0.1',1))
                elif is_selected:self._invoke('RetryCallConnection','(s)',(peer,))
                else:
                    self._invoke('SelectCallRelayPhone','(ss)',(peer,'Paired phone'))
            action.connect('clicked',selected);group.add(action)

    def _pairing_controls(self,group,ready):
        actions=Adw.WrapBox(child_spacing=8,line_spacing=8,margin_bottom=12) if hasattr(Adw,'WrapBox') else \
            Gtk.Box(orientation=Gtk.Orientation.VERTICAL if self.compact else Gtk.Orientation.HORIZONTAL,spacing=8,margin_bottom=8)
        create=Gtk.Button(label='Create Phone Invitation',sensitive=ready)
        create.connect('clicked',lambda *_:self._invoke('PairMessageDevice','(sss)',('create','','')))
        actions.append(create)
        for operation,title in [('accept','Accept an Invitation…'),('finish','Finish Pairing…')]:
            button=Gtk.Button(label=title,sensitive=ready)
            button.connect('clicked',lambda _button,op=operation:self._import_pairing(op))
            actions.append(button)
        for setter in (actions.set_margin_start,actions.set_margin_end,actions.set_margin_top):setter(12)
        group.add(Gtk.ListBoxRow(activatable=False,selectable=False,child=actions))
        pairing=self.state.get('pairing') or {}
        if pairing.get('fingerprint'):
            self._row(group,'This device’s verification code',' '.join(pairing['fingerprint'][i:i+4] for i in range(0,64,4)))
        if pairing.get('document'):
            export=Gtk.Button(label='Save Invitation…' if pairing['kind']=='offer' else 'Save Pairing Response…',sensitive=ready,halign=Gtk.Align.START)
            def save(*_):
                chooser=Gtk.FileDialog(title='Save pairing document',initial_name='luma-pairing.json')
                def saved(dialog,result):
                    try:
                        file=dialog.save_finish(result)
                        file.replace_contents(pairing['document'].encode(),None,False,Gio.FileCreateFlags.PRIVATE,None)
                    except GLib.Error:pass
                chooser.save(self,None,saved)
            export.connect('clicked',save);group.add(export)
        if pairing.get('kind')=='complete':self._row(group,'Pairing completed','Choose the paired phone and its local connection address below.')

    def _import_pairing(self,operation):
        chooser=Gtk.FileDialog(title='Open invitation' if operation=='accept' else 'Open pairing response')
        def opened(dialog,result):
            try:
                file=dialog.open_finish(result)
                path=file.get_path()
                if not path:raise ValueError('local document required')
                with open(path,'rb') as stream:data=stream.read(32769)
                if not 0<len(data)<=32768:raise ValueError('invalid document size')
                document=data.decode('utf-8')
            except (GLib.Error,OSError,ValueError):return
            confirm=Adw.AlertDialog(heading='Verify the other device',body='Enter the full verification code shown on the other device. Accepting allows it to read conversations; sending is not enabled.' if operation=='accept' else 'Enter the full verification code shown on the phone. This checks that the response came from the device you chose.')
            entry=Gtk.Entry(placeholder_text='Other device’s verification code')
            confirm.set_extra_child(entry);confirm.add_response('cancel','Cancel');confirm.add_response('pair','Verify and Pair');confirm.set_close_response('cancel')
            confirm.connect('response',lambda _dialog,response:self._invoke('PairMessageDevice','(sss)',(operation,document,''.join(entry.get_text().split()))) if response=='pair' else None)
            confirm.present(self)
        chooser.open(self,None,opened)

    def _message_endpoint(self,peer,sharing):
        dialog=Adw.AlertDialog(heading='Share conversations' if sharing else 'Use this phone for Messages',
            body='Allow this paired device to read conversations while sharing is on. Enter this device’s exact local network address.' if sharing else
                 'Use this paired phone’s existing permissions when Messages next opens. Close and reopen Messages after choosing. Enter the phone’s local network address.')
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
        label=Gtk.Entry(placeholder_text='Phone name',text='My phone')
        address=Gtk.Entry(placeholder_text='Local IP address')
        port=Gtk.SpinButton.new_with_range(1024,65535,1);port.set_value(18444)
        if not sharing:box.append(label)
        box.append(address);box.append(Gtk.Label(label='Connection port',xalign=0));box.append(port)
        dialog.set_extra_child(box);dialog.add_response('cancel','Cancel');dialog.add_response('confirm','Start Sharing' if sharing else 'Use Phone')
        dialog.set_close_response('cancel')
        def confirmed(_dialog,response):
            if response!='confirm':return
            if sharing:self._invoke('StartMessageSharing','(ssu)',(peer,address.get_text(),port.get_value_as_int()))
            else:self._invoke('SelectMessagePhone','(sssu)',(peer,label.get_text(),address.get_text(),port.get_value_as_int()))
        dialog.connect('response',confirmed);dialog.present(self)

    def _time_text(self,value):
        return when_text(value) if isinstance(value,(int,float)) and not isinstance(value,bool) else 'Unavailable'
    def _confirm_signout(self,target,label):
        if target=='others':
            count=sum(not row.get('current') for row in (self.state.get('sessions') or {}).get('items',[]))
            body=f'Sign out of {count} other sessions. This device stays signed in. Downloaded files stay on those devices.'
        elif target=='current':body='Your files and unsynced work stay on this device. Anything available only in Luma will be unavailable until you sign back in.'
        else:body='Downloaded files stay on that computer. It stops using the relay once the sign-out is confirmed.'
        dialog=Adw.AlertDialog(heading=f'Sign out of {label}?',body=body)
        dialog.add_response('cancel','Cancel');dialog.add_response('signout','Sign Out')
        dialog.set_response_appearance('signout',Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel');dialog.set_close_response('cancel')
        dialog.connect('response',lambda _dialog,response:self._invoke('SignOut','(s)',(target,)) if response=='signout' else None);dialog.present(self)


class ConnectApplication(Adw.Application):
    def __init__(self):super().__init__(application_id=APP_ID)
    def do_startup(self):
        Adw.Application.do_startup(self);install_appkit()
        display=Gdk.Display.get_default()
        if display is not None:
            # The glyphs Connect draws with ship beside it; a checkout finds them next to the package.
            here=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'data','icons')
            if os.path.isdir(here):Gtk.IconTheme.get_for_display(display).add_search_path(here)
            # The kit owns this sheet, so it follows the surface treatment.
            add_style_builder(lambda _appearance: STYLE)
    def do_activate(self):
        window=self.props.active_window or ConnectWindow(self);window.present()

def main():
    import sys
    return ConnectApplication().run(sys.argv)
if __name__=='__main__':raise SystemExit(main())
