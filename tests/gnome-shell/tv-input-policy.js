// SPDX-License-Identifier: GPL-2.0-or-later
// TV posture input: remotes and gamepads onto the TV's keys, the game-input
// policy, and the wiring that keeps the service to TV posture.
// Run: gjs -m tv-input-policy.js js/ui/lumaPresentation/tvInputModel.js \
//        js/ui/lumaPresentation/tvTokens.js js/ui/lumaPresentation/tvInput.js \
//        js/ui/lumaPresentation/tv.js
// (node tv-input-policy.js ... works too).
const gjs = typeof globalThis.ARGV !== 'undefined';
const args = gjs ? globalThis.ARGV : process.argv.slice(2);
const GLib = gjs ? (await import('gi://GLib')).default : null;
const fs = gjs ? null : await import('node:fs');
const cwd = gjs ? GLib.get_current_dir() : process.cwd();
const absolute = path => (path.startsWith('/') ? path : `${cwd}/${path}`);
const read = path => (gjs ? new TextDecoder().decode(GLib.file_get_contents(path)[1])
    : fs.readFileSync(path, 'utf8'));

function assert(value, message) {
    if (!value)
        throw new Error(`FAIL: ${message}`);
}

const M = await import(`file://${absolute(args[0])}`);
const {tv: tokens} = await import(`file://${absolute(args[1])}`);
const service = read(args[2]);
const home = read(args[3]);
const input = tokens.input;

// The TV's keys: arrows, Return = OK, Escape = Back, Menu.
assert(M.TV_KEYS.ok === 'Return' && M.TV_KEYS.back === 'Escape' && M.TV_KEYS.menu === 'Menu', 'TV keys');
assert(M.DIRECTIONS.every(d => M.TV_KEYS[d]), 'every direction is a key');

// Remote keys that are not TV keys become them; CEC's through rc-cec.
const remote = {XF86Back: 'back', XF86Close: 'back', XF86OK: 'ok', XF86Select: 'ok',
    XF86HomePage: 'home', XF86RootMenu: 'home', XF86MenuKB: 'menu', XF86ContextMenu: 'menu'};
for (const [name, action] of Object.entries(remote))
    assert(M.REMOTE_KEYS[name] === action, `${name} is ${action}`);
assert(!Object.keys(M.REMOTE_KEYS).some(name => Object.values(M.TV_KEYS).includes(name)),
    'TV keys are never re-sent (no loop through the virtual keyboard)');

// Gamepad buttons.
const B = M.BUTTON;
assert(M.buttonAction(B.SOUTH) === 'ok', 'South is OK by default');
assert(M.buttonAction(B.EAST) === 'back' && M.buttonAction(B.SELECT) === 'back', 'East and Select are Back');
assert(M.buttonAction(B.EAST, 'east') === 'ok' && M.buttonAction(B.SOUTH, 'east') === 'back',
    'Nintendo layout: East is OK, South is Back');
assert(M.buttonAction(B.SELECT, 'east') === 'back' && M.buttonAction(B.START, 'east') === 'menu', 'the rest stays');

// Which face button is OK: the setting wins; automatic follows the layout.
const xbox = M.padIdentity('030000005e0400008e02000014010000');
assert(xbox.vendor === 0x045e && xbox.product === 0x028e, 'vendor and product from the GUID');
assert(M.padIdentity('nope') === null, 'a malformed GUID');
const pro = M.padIdentity('050000007e0500000920000001800000');
assert(pro.vendor === 0x057e && pro.product === 0x2009, 'Switch Pro Controller');
assert(M.confirmButton('automatic', xbox, 'Microsoft X-Box 360 pad') === 'south', 'Xbox pads: South');
assert(M.confirmButton('automatic', pro, 'Pro Controller') === 'east', 'Nintendo pads: East');
assert(M.confirmButton('automatic', {vendor: 0x0810, product: 0xe501}, 'usb gamepad') === 'east', 'known SNES USB pad');
assert(M.confirmButton('automatic', null, '8BitDo SN30 Pro (Switch)') === 'east', 'by name');
assert(M.confirmButton('east', xbox, 'Microsoft X-Box 360 pad') === 'east', 'the setting wins over a misreported pad');
assert(M.confirmButton('south', pro, 'Pro Controller') === 'south', 'and over a Nintendo pad');
assert(M.buttonAction(B.START) === 'menu', 'Start is Menu');
assert(M.buttonAction(B.MODE) === 'home', 'Guide is Home');
assert(M.buttonAction(B.DPAD_UP) === 'up' && M.buttonAction(B.DPAD_RIGHT) === 'right', 'D-pad buttons move');
for (const code of [B.NORTH, B.WEST, B.TL, B.TR, B.THUMBL, B.THUMBR])
    assert(M.buttonAction(code) === null, `button ${code} is unassigned`);
assert(M.hatDirection(M.AXIS.HAT0X, -1) === 'left' && M.hatDirection(M.AXIS.HAT0Y, 1) === 'down', 'hat D-pad');
assert(M.hatDirection(M.AXIS.HAT0X, 0) === null && M.hatDirection(M.AXIS.X, 1) === null, 'hat centre and other axes');

// The stick: dominant axis past press, held until inside release.
assert(input.stick_release < input.stick_press && input.stick_press < 1, 'stick thresholds');
assert(M.stickDirection(0.2, 0.1, null, input) === null, 'dead zone');
assert(M.stickDirection(0.9, 0.3, null, input) === 'right', 'dominant axis');
assert(M.stickDirection(-0.3, -0.8, null, input) === 'up', 'up is negative y');
const between = (input.stick_press + input.stick_release) / 2;
assert(M.stickDirection(between, 0, 'right', input) === 'right', 'held past release');
assert(M.stickDirection(between, 0, null, input) === null, 'not taken before press');
assert(M.stickDirection(input.stick_release / 2, 0, 'right', input) === null, 'let go inside release');
assert(M.stickDirection(-0.9, 0, 'right', input) === 'left', 'flipped across');

// TV-paced repeat.
assert(M.repeatDelay(1, input) === input.repeat_delay_ms && M.repeatDelay(5, input) === input.repeat_interval_ms,
    'one step, a delay, then the interval');
assert(input.repeat_interval_ms >= 80 && input.repeat_delay_ms > input.repeat_interval_ms, 'a TV pace');

// Who gets the pad.
const tvApp = {presentation: 'tv', claimed: false};
const game = {presentation: '', claimed: false};
const claimed = {presentation: 'tv', claimed: true};
const ctx = (window, extra = {}) => ({shellFocus: false, keyboardVisible: false, window, ...extra});
assert(M.translates('ok', ctx(null)), 'nothing focused: the Shell');
assert(M.translates('ok', ctx(game, {shellFocus: true})), 'a Shell surface');
assert(M.translates('up', ctx(tvApp)), 'a TV app');
assert(!M.translates('up', ctx(game)) && !M.translates('ok', ctx(game)), 'any other app reads the pad itself');
assert(M.translates('home', ctx(game)), 'Home leaves a game');
assert(!M.translates('up', ctx(claimed)) && !M.translates('home', ctx(claimed)), 'a claim keeps the whole pad');
assert(M.translates('up', ctx(claimed, {keyboardVisible: true})), 'the TV keyboard always takes the pad');

// The service: only in TV posture, every remote key taken, keys from a
// virtual keyboard, libmanette optional.
assert(/_enter\(\) \{[\s\S]*?this\._input = new TvInput\(/.test(home), 'TV posture starts the service');
assert(/_leave\(\) \{[\s\S]*?this\._input\?\.destroy\(\)/.test(home), 'leaving TV posture ends it');
assert(/for \(const name of Object\.keys\(REMOTE_KEYS\)\)[\s\S]*?grab_accelerator\(name, Meta\.KeyBindingFlags\.IGNORE_AUTOREPEAT\)/
    .test(service), 'every remote key is taken, without autorepeat');
assert(/destroy\(\) \{[\s\S]*?ungrab_accelerator/.test(service), 'and given back');
assert(/create_virtual_device\(Clutter\.InputDeviceType\.KEYBOARD_DEVICE\)/.test(service), 'a virtual keyboard');
assert(/await import\('gi:\/\/Manette\?version=0\.2'\)[\s\S]*?catch/.test(service), 'libmanette is optional');
assert(/translates\(action, this\._context\(\)\)/.test(service), 'every pad key asks the policy');
assert(service.includes('org.projectluma.Shell.Remote1') && service.includes('ClaimGamepadsAsync'),
    'the claim contract');
assert(!/EVIOCGRAB|grab_device/.test(service), 'the pad is never grabbed from games');
assert(/settings_schema\.has_key\(CONFIRM_KEY\)/.test(service) && /changed::\$\{CONFIRM_KEY\}/.test(service),
    'the confirm setting is optional and applied live');
assert(/buttonAction\(code, pad\.confirm\)/.test(service), 'per pad');

print('tv-input-policy: ok');

function print(message) {
    if (gjs)
        globalThis.print(message);
    else
        console.log(message);
}
