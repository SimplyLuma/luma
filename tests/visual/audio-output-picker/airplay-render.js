// SPDX-License-Identifier: MPL-2.0
// Renders the Sound Output detail and the AirPlay picker in each state.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {PopupAnimation} from 'resource:///org/gnome/shell/ui/boxpointer.js';

const OUT = GLib.getenv('ORACLE_OUT');

function extents(actor, pad = 24) {
    const [x, y] = actor.get_transformed_position();
    const [w, h] = actor.get_transformed_size();
    return [Math.max(0, Math.round(x - pad)), Math.max(0, Math.round(y - pad)),
        Math.round(w + 2 * pad), Math.round(h + 2 * pad)];
}

function dump(actor) {
    return {
        type: actor.constructor.name, style: actor.style_class ?? '', text: actor.text ?? undefined,
        icon: actor.icon_name ?? actor.gicon?.to_string?.() ?? undefined,
        box: [Math.round(actor.x), Math.round(actor.y), Math.round(actor.width), Math.round(actor.height)],
        children: actor.get_children().filter(c => c.visible).map(dump),
    };
}

function scenario(name) {
    Gio.DBus.session.call_sync('org.projectluma.AudioDevices', '/org/projectluma/RenderTest',
        'org.projectluma.RenderTest', 'SetScenario', new GLib.Variant('(s)', [name]), null,
        Gio.DBusCallFlags.NONE, 2000, null);
}

export default async function ({Main, Scripting, shot}) {
    const quick = Main.panel.statusArea.quickSettings;
    const slider = quick._volumeOutput._output;
    const log = [];
    const write = () => GLib.file_set_contents(`${OUT}/render.json`, JSON.stringify(log, null, 1));
    if (GLib.getenv('AUDIO_NO_SERVICE')) {
        quick.menu.open(PopupAnimation.NONE);
        await Scripting.sleep(1500);
        slider.menu.open(PopupAnimation.NONE);
        await Scripting.sleep(1500);
        log.push({state: 'no-service', menuEnabled: slider.menuEnabled, airplayVisible: slider._airPlayItem.visible,
            devices: [...slider._deviceItems.values()].map(i => i.label.text)});
        await shot('00-no-service', ...extents(quick.menu._boxPointer ?? quick.menu.actor));
        write();
        return;
    }

    scenario('empty');
    quick.menu.open(PopupAnimation.NONE);
    await Scripting.sleep(1500);
    slider.menu.open(PopupAnimation.NONE);
    await Scripting.sleep(1500);
    const panel = quick.menu._boxPointer ?? quick.menu.actor;
    log.push({state: 'detail', menuEnabled: slider.menuEnabled,
        devices: [...slider._deviceItems.values()].map(i => i.label.text),
        hidden: [...slider._hiddenDeviceIds], airplayVisible: slider._airPlayItem.visible});
    await shot('01-sound-output', ...extents(panel));

    slider._airPlayItem.menu.open(PopupAnimation.NONE);
    await Scripting.sleep(1500);
    await shot('02-airplay-searching', ...extents(panel));

    scenario('full');
    await Scripting.sleep(1500);
    log.push({state: 'receivers', tree: dump(slider.menu.box)});
    await shot('03-airplay-receivers', ...extents(panel));

    const office = slider._airPlayItem._receivers.find(r => r.name === 'Office Mac');
    slider._airPlayItem._choose(office);
    await Scripting.sleep(2500);
    const dialog = Main.layoutManager.modalDialogGroup.get_children().find(a => a._entry && a._receiver);
    log.push({state: 'dialog', found: Boolean(dialog), quickOpen: quick.menu.isOpen});
    const monitor = Main.layoutManager.primaryMonitor;
    await shot('04-password-dialog', monitor.x + monitor.width / 2 - 360, monitor.y + monitor.height / 2 - 260, 720, 520);
    const instance = dialog;
    if (instance) {
        instance._entry.set_text('not-it');
        await instance._onConnect();
        await Scripting.sleep(800);
        log.push({state: 'dialog-error', error: instance._errorLabel.text, tree: dump(instance)});
        await shot('05-password-wrong', monitor.x + monitor.width / 2 - 360, monitor.y + monitor.height / 2 - 260, 720, 520);
        instance.close();
    }
    write();
}
