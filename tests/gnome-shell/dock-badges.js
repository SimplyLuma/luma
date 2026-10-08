// SPDX-License-Identifier: GPL-2.0-or-later
// Rules for unread badges on dock icons (js/ui/lumaDockBadgeModel.js).
// Run: gjs -m dock-badges.js path/to/lumaDockBadgeModel.js
import GLib from 'gi://GLib';
import System from 'system';
const M = await import(GLib.filename_to_uri(GLib.canonicalize_filename(ARGV[0], GLib.get_current_dir()), null));

let failures = 0;
const check = (name, ok, detail = '') => {
    if (!ok) {
        failures++;
        printerr(`FAIL ${name} ${detail}`);
    }
};
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// The decided look and the owner's defaults.
check('deeper Ember fill', M.BADGE_FILL === '#c24a0b');
check('white ink', M.BADGE_INK === '#ffffff');
// The count is white at 9.5px, so the fill has to meet WCAG AA (4.5:1).
const channel = value => {
    const c = value / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};
const luminance = hex => 0.2126 * channel(parseInt(hex.slice(1, 3), 16)) +
    0.7152 * channel(parseInt(hex.slice(3, 5), 16)) +
    0.0722 * channel(parseInt(hex.slice(5, 7), 16));
const contrast = (a, b) => {
    const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (high + 0.05) / (low + 0.05);
};
check('white on the fill meets AA', contrast(M.BADGE_INK, M.BADGE_FILL) >= 4.5,
    contrast(M.BADGE_INK, M.BADGE_FILL).toFixed(3));
check('no notification-count fallback by default', M.NOTIFICATION_COUNT_FALLBACK === false);
check('a window asking for attention gives its app a dot', M.URGENT_WINDOWS_DOT === true);

// Agent values: an unsigned count or 'dot'.
check('count 1', same(M.badgeFromAgentValue(1), {kind: 'count', count: 1}));
check('count 128 keeps the real number', same(M.badgeFromAgentValue(128), {kind: 'count', count: 128}));
check('int64 count', same(M.badgeFromAgentValue(7n), {kind: 'count', count: 7}));
check('dot', same(M.badgeFromAgentValue('dot'), {kind: 'dot'}));
for (const none of [0, -3, null, undefined, '', '5', true, NaN, Infinity, {}, []])
    check(`no badge for ${String(none)}`, M.badgeFromAgentValue(none) === null);

// LauncherEntry.
check('visible count', same(M.badgeFromLauncherEntry({'count': 4, 'count-visible': true}), {kind: 'count', count: 4}));
check('hidden count is nothing', M.badgeFromLauncherEntry({'count': 4, 'count-visible': false}) === null);
check('urgent without a visible count is a dot', same(M.badgeFromLauncherEntry({'count': 4, 'urgent': true}), {kind: 'dot'}));
check('visible count wins over urgent', same(M.badgeFromLauncherEntry({'count': 2, 'count-visible': true, 'urgent': true}), {kind: 'count', count: 2}));
check('visible zero with urgent is a dot', same(M.badgeFromLauncherEntry({'count': 0, 'count-visible': true, 'urgent': true}), {kind: 'dot'}));
check('empty entry', M.badgeFromLauncherEntry({}) === null);

// Precedence.
const launcher = {'count': 9, 'count-visible': true};
check('agent wins', same(M.resolveBadge({agent: 3, launcher}), {kind: 'count', count: 3}));
check('agent zero owns the badge', M.resolveBadge({agent: 0, launcher}) === null);
check('no agent value falls to LauncherEntry', same(M.resolveBadge({agent: undefined, launcher}), {kind: 'count', count: 9}));
check('switched off', M.resolveBadge({enabled: false, agent: 3, launcher}) === null);
check('notifications alone are nothing by default', M.resolveBadge({notifications: 4}) === null);
check('urgent windows alone are a dot', same(M.resolveBadge({urgent: true}), {kind: 'dot'}));
check('nothing', M.resolveBadge({}) === null);

// Text, names, pop.
check('1', M.badgeText({kind: 'count', count: 1}) === '1');
check('99', M.badgeText({kind: 'count', count: 99}) === '99');
check('100 is 99+', M.badgeText({kind: 'count', count: 100}) === '99+');
check('dot has no text', M.badgeText({kind: 'dot'}) === '');
check('name with count', M.badgeAccessibleName('Charlie', {kind: 'count', count: 128}) === 'Charlie, 128 unread');
check('name with dot', M.badgeAccessibleName('Calendar', {kind: 'dot'}) === 'Calendar, new activity');
check('name alone', M.badgeAccessibleName('Messages', null) === 'Messages');
check('pop on appear', M.badgeShouldPop(null, {kind: 'count', count: 1}));
check('pop on increase', M.badgeShouldPop({kind: 'count', count: 1}, {kind: 'count', count: 2}));
check('pop dot to count', M.badgeShouldPop({kind: 'dot'}, {kind: 'count', count: 1}));
check('no pop on decrease', !M.badgeShouldPop({kind: 'count', count: 3}, {kind: 'count', count: 2}));
check('no pop on same', !M.badgeShouldPop({kind: 'count', count: 3}, {kind: 'count', count: 3}));
check('no pop on removal', !M.badgeShouldPop({kind: 'count', count: 3}, null));
check('no pop count to dot', !M.badgeShouldPop({kind: 'count', count: 3}, {kind: 'dot'}));

// Pop curve: .6 -> 1.18 -> 1.
check('pop starts at .6', Math.abs(M.popScale(0) - 0.6) < 1e-6);
check('pop peaks at 1.18', Math.abs(M.popScale(0.55) - 1.18) < 1e-6);
check('pop ends at 1', Math.abs(M.popScale(1) - 1) < 1e-6);
let previous = M.popScale(0), rising = true;
for (let i = 1; i <= 55; i++) {
    const value = M.popScale(i / 100);
    rising &&= value >= previous - 1e-9;
    previous = value;
}
check('pop rises to its peak', rising);

// Geometry at the shelf's 36 artwork (reference 32 icon inset 2).
const art = {x: 100, y: 200, width: 36, height: 36};
for (const scale of [1, 2]) {
    const a = {x: art.x * scale, y: art.y * scale, width: 36 * scale, height: 36 * scale};
    const right = (100 + 36 - 2) * scale, top = (200 + 2) * scale;
    const one = M.badgeBox({kind: 'count', count: 1}, a, 5 * scale, scale);
    check(`@${scale} pill height 15`, one.height - 2 * one.ring === 15 * scale, JSON.stringify(one));
    check(`@${scale} pill min width 15`, one.width - 2 * one.ring === 15 * scale, JSON.stringify(one));
    check(`@${scale} ring 2`, one.ring === 2 * scale);
    check(`@${scale} pill top 5 above the icon`, top - (one.y + one.ring) === 5 * scale);
    check(`@${scale} pill right 5 past the icon`, one.x + one.width - one.ring - right === 5 * scale);
    const wide = M.badgeBox({kind: 'count', count: 128}, a, 18 * scale, scale);
    check(`@${scale} wide pill keeps its right edge`, wide.x + wide.width === one.x + one.width);
    check(`@${scale} wide pill grows leftward`, wide.x < one.x && wide.width - 2 * wide.ring === 26 * scale);
    const dot = M.badgeBox({kind: 'dot'}, a, 0, scale);
    check(`@${scale} dot 9`, dot.width - 2 * dot.ring === 9 * scale && dot.height - 2 * dot.ring === 9 * scale);
    check(`@${scale} dot 3 past the corner`, top - (dot.y + dot.ring) === 3 * scale && dot.x + dot.width - dot.ring - right === 3 * scale);
    // The next icon starts DASH_TILE_GAP (6) after the artwork: the ring never reaches it.
    check(`@${scale} never touches the neighbour`, wide.x + wide.width <= (art.x + 36 + 6) * scale);
}
const phone = M.badgeBox({kind: 'count', count: 3}, {x: 0, y: 0, width: 58, height: 58}, 7, 1, M.PHONE_METRICS);
check('phone pill 22 tall with a 2.5 ring', phone.height === 22 + 2 * phone.ring && phone.ring === 3);

// LauncherEntry URIs.
check('desktop id from uri', M.desktopIdFromUri('application://discord.desktop') === 'discord.desktop');
check('flatpak id from uri', M.desktopIdFromUri('application://com.slack.Slack.desktop') === 'com.slack.Slack.desktop');
for (const bad of ['discord.desktop', 'application://../x.desktop', 'application://a/b.desktop', 'application://x', 42])
    check(`reject ${bad}`, M.desktopIdFromUri(bad) === null);
check('app id to desktop id', M.desktopIdForAppId('org.projectluma.Messages') === 'org.projectluma.Messages.desktop');
check('desktop id to app id', M.appIdForDesktopId('org.projectluma.Messages.desktop') === 'org.projectluma.Messages');

if (failures) {
    printerr(`dock-badges: ${failures} failed`);
    System.exit(1);
}
print('dock-badges: PASS');
