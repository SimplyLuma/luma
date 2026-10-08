// SPDX-License-Identifier: MPL-2.0
// Opens the dock menu for apps with and without background agents, and
// captures each state of the "Run in the Background" row.
import GLib from 'gi://GLib';
import {PopupAnimation} from 'resource:///org/gnome/shell/ui/boxpointer.js';

const OUT = GLib.getenv('ORACLE_OUT');

function extents(actor, pad = 20) {
    const [x, y] = actor.get_transformed_position();
    const [w, h] = actor.get_transformed_size();
    return [Math.max(0, Math.round(x - pad)), Math.max(0, Math.round(y - pad)),
        Math.round(w + 2 * pad), Math.round(h + 2 * pad)];
}

function dump(actor) {
    return {
        type: actor.constructor.name, style: actor.style_class ?? '', text: actor.text ?? undefined,
        box: [Math.round(actor.x), Math.round(actor.y), Math.round(actor.width), Math.round(actor.height)],
        children: actor.get_children().filter(c => c.visible).map(dump),
    };
}

function findIcon(root, appId) {
    const stack = [root];
    while (stack.length) {
        const actor = stack.pop();
        if (actor.app?.get_id?.() === appId && typeof actor.popupMenu === 'function')
            return actor;
        stack.push(...actor.get_children());
    }
    return null;
}

export default async function ({Main, Scripting, shot}) {
    const log = [];
    const write = () => GLib.file_set_contents(`${OUT}/render.json`, JSON.stringify(log, null, 1));
    const cases = [
        ['01-messages-on', 'org.projectluma.Messages.desktop', null],
        ['02-messages-turned-off', 'org.projectluma.Messages.desktop', false],
        ['03-weather-off', 'org.projectluma.Weather.desktop', null],
        ['04-weather-turned-on', 'org.projectluma.Weather.desktop', true],
        ['05-photos-no-agent', 'org.projectluma.Photos.desktop', null],
    ];
    for (const [name, appId, toggle] of cases) {
      try {
        const icon = findIcon(Main.layoutManager.uiGroup, appId);
        if (!icon) {
            log.push({name, error: `no dock icon for ${appId}`});
            continue;
        }
        icon.popupMenu();
        await Scripting.sleep(1200);
        const menu = icon._menu;
        if (toggle !== null) {
            // The row's own toggle path, as a click on it would take.
            menu._backgroundItem._switch.toggle();
            menu._backgroundItem.emit('toggled', menu._backgroundItem.state);
            await Scripting.sleep(1200);
        }
        log.push({name, rowVisible: menu._backgroundItem.visible, switchState: menu._backgroundItem.state,
            subtitle: menu._backgroundItem._subtitle.text, tree: dump(menu.box)});
        await shot(name, ...extents(menu._boxPointer ?? menu.actor));
        menu.close(PopupAnimation.NONE);
        await Scripting.sleep(400);
      } catch (error) {
        log.push({name, error: `${error}`, stack: error.stack});
      }
      write();
    }
    write();
}
