// SPDX-License-Identifier: Apache-2.0
// TV posture input: the pure rules that turn every controller into the TV's
// keys. tvInput.js applies them in the Shell, so the TV home, the TV keyboard
// and every application (LumaUI's TvFocus) read keys and nothing else.
// docs/developer-platform/tv-mode.md ("Remotes and gamepads").

// The TV's keys, as keysym names: what every TV surface reads. Home is not a
// key: the Shell opens the TV home itself.
export const TV_KEYS = Object.freeze({
    up: 'Up', down: 'Down', left: 'Left', right: 'Right',
    ok: 'Return', back: 'Escape', menu: 'Menu',
});

export const DIRECTIONS = Object.freeze(['up', 'down', 'left', 'right']);

// Remote keys that are not TV keys, by keysym name. Arrows and Return already
// are; these become them. HDMI-CEC keys arrive through the kernel's rc-cec
// keymap (OK = KEY_OK, Back = KEY_EXIT, Root Menu = KEY_ROOT_MENU, Contents
// Menu = KEY_MENU); Bluetooth and 2.4 GHz remotes send the consumer-page keys.
export const REMOTE_KEYS = Object.freeze({
    XF86Back: 'back',           // KEY_BACK (consumer AC Back)
    XF86Close: 'back',          // KEY_EXIT (CEC Back/Exit)
    XF86OK: 'ok',               // KEY_OK (CEC Select)
    XF86Select: 'ok',           // KEY_SELECT (consumer Menu Pick)
    XF86HomePage: 'home',       // KEY_HOMEPAGE (consumer AC Home)
    XF86RootMenu: 'home',       // KEY_ROOT_MENU (CEC Device Root Menu)
    XF86MenuKB: 'menu',         // KEY_MENU (consumer Menu, CEC Contents Menu)
    XF86ContextMenu: 'menu',    // KEY_CONTEXT_MENU
});

// Gamepad controls, as libmanette reports them: Linux input codes after its
// SDL-style mapping, so every supported pad has the same layout.
export const BUTTON = Object.freeze({
    SOUTH: 0x130, EAST: 0x131, NORTH: 0x133, WEST: 0x134, TL: 0x136, TR: 0x137,
    SELECT: 0x13a, START: 0x13b, MODE: 0x13c, THUMBL: 0x13d, THUMBR: 0x13e,
    DPAD_UP: 0x220, DPAD_DOWN: 0x221, DPAD_LEFT: 0x222, DPAD_RIGHT: 0x223,
});

export const AXIS = Object.freeze({X: 0x00, Y: 0x01, HAT0X: 0x10, HAT0Y: 0x11});

// Controllers whose OK is the right face button (Nintendo layout): Nintendo's
// own (Switch Pro, Joy-Con, the Online SNES/N64 pads, and 8BitDo pads in
// Switch mode, which report as a Pro Controller), and SNES-style USB pads.
export const NINTENDO_LAYOUT = Object.freeze({
    vendors: Object.freeze([0x057e]),
    devices: Object.freeze(['0079:0011', '0583:2060', '0810:e501', '081f:e401', '12bd:d015']),
    names: /nintendo|switch|joy-?con|pro controller|snes|famicom/i,
});

// Vendor and product from an SDL controller GUID as libmanette reports it
// (little-endian 16-bit fields: bus, CRC, vendor, 0, product, 0, version, 0).
export function padIdentity(guid) {
    if (!/^[0-9a-f]{32}$/i.test(guid ?? '')) return null;
    const word = index => parseInt(guid.slice(index + 2, index + 4) + guid.slice(index, index + 2), 16);
    return {vendor: word(8), product: word(16)};
}

export function nintendoLayout(identity, name) {
    if (identity) {
        const hex = value => value.toString(16).padStart(4, '0');
        if (NINTENDO_LAYOUT.vendors.includes(identity.vendor)) return true;
        if (NINTENDO_LAYOUT.devices.includes(`${hex(identity.vendor)}:${hex(identity.product)}`)) return true;
    }
    return NINTENDO_LAYOUT.names.test(name ?? '');
}

// Which face button is OK: the setting (luma-shell-state `tv-confirm-button`)
// wins; `automatic` follows the controller's layout. A pad that claims to be
// an Xbox pad but has Nintendo labels needs the explicit `east`.
export function confirmButton(setting, identity, name) {
    if (setting === 'south' || setting === 'east') return setting;
    return nintendoLayout(identity, name) ? 'east' : 'south';
}

// The confirm face button is OK and the other of South/East is Back; Select
// (Back/View) is Back, Start is the system's pause menu (the quick panel, from
// any app or the TV home), the D-pad moves and Guide (Mode) goes home. A
// remote's Menu key stays Menu (an item's menu); a pad holds OK for that. North, West, the shoulders, triggers, stick clicks and the right
// stick are not assigned.
export function buttonAction(code, confirm = 'south') {
    switch (code) {
    case BUTTON.SOUTH: return confirm === 'east' ? 'back' : 'ok';
    case BUTTON.EAST: return confirm === 'east' ? 'ok' : 'back';
    case BUTTON.SELECT: return 'back';
    case BUTTON.START: return 'pause';
    case BUTTON.MODE: return 'home';
    case BUTTON.DPAD_UP: return 'up';
    case BUTTON.DPAD_DOWN: return 'down';
    case BUTTON.DPAD_LEFT: return 'left';
    case BUTTON.DPAD_RIGHT: return 'right';
    default: return null;
    }
}

// A D-pad reported as a hat: -1, 0 or 1 on each axis.
export function hatDirection(axis, value) {
    if (axis === AXIS.HAT0X) return value < 0 ? 'left' : value > 0 ? 'right' : null;
    if (axis === AXIS.HAT0Y) return value < 0 ? 'up' : value > 0 ? 'down' : null;
    return null;
}

// The left stick as a D-pad (x, y from -1 to 1, y down). It takes a direction
// past `press` on its dominant axis and keeps it until it falls back inside
// `release` (hysteresis), so a stick resting near the edge does not chatter.
export function stickDirection(x, y, held, input) {
    if (held) {
        const value = held === 'left' || held === 'right' ? x : y;
        const sign = held === 'left' || held === 'up' ? -1 : 1;
        if (value * sign >= input.stick_release) return held;
    }
    const ax = Math.abs(x), ay = Math.abs(y);
    if (Math.max(ax, ay) < input.stick_press) return null;
    if (ax >= ay) return x < 0 ? 'left' : 'right';
    return y < 0 ? 'up' : 'down';
}

// A held direction steps once, then again after `repeat_delay_ms`, then every
// `repeat_interval_ms`: a TV pace, independent of the keyboard's typing repeat.
export function repeatDelay(steps, input) {
    return steps <= 1 ? input.repeat_delay_ms : input.repeat_interval_ms;
}

// Whether a gamepad press becomes a TV key, or is left to the application
// that reads the pad itself (a game). `context`:
//   shellFocus      a Shell surface (TV home, a dialog) has the key focus
//   keyboardVisible the TV keyboard is up (it takes the remote's keys)
//   window          the focused window: {presentation, claimed}, or null
// TV apps (`X-Luma-TV-Presentation=tv`) are built for the remote and get keys
// unless they claimed the gamepads; any other application keeps the pad raw,
// as on a desktop. Home (Guide, or Select+Start) and Start (the pause menu)
// work in every application except one that claimed the gamepads: a game
// keeps Start, and only a held Guide (`holdsHome`) opens the quick panel there.
export function translates(action, context) {
    const {shellFocus, keyboardVisible, window} = context;
    if (keyboardVisible || shellFocus || !window) return true;
    if (window.claimed) return false;
    if (action === 'home' || action === 'pause') return true;
    return window.presentation === 'tv';
}

// A held Guide opens the quick panel everywhere, a game that claimed the
// gamepads too (its short Guide press stays the game's).
export function holdsHome(context) {
    return !translates('home', context);
}
