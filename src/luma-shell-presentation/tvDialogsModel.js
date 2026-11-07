// SPDX-License-Identifier: Apache-2.0
// TV posture of the Shell's system dialogs: the pure rules (no GI imports, so
// tests run them under gjs or node). tvDialogs.js applies them to every
// ModalDialog: Wi-Fi and VPN secrets, keyring and administrator prompts, end
// session, mounts, the run dialog, access prompts. Lengths are TV units
// (tvTokens.js): one pixel of a 1920x1080 layout.
// docs/developer-platform/tv-mode.md ("System dialogs").

// What a remote key does in a dialog. The TV keyboard takes these keys while
// it is open (it grabs them as accelerators), so a dialog sees them only with
// the keyboard put away.
export const DIALOG_KEYS = Object.freeze({
    Up: 'up', Down: 'down', Left: 'left', Right: 'right',
    Return: 'ok', KP_Enter: 'ok', ISO_Enter: 'ok',
    Escape: 'back', XF86Back: 'back',
});

export function dialogKey(name) {
    return DIALOG_KEYS[name] ?? null;
}

// A label's part from its style classes: the title, a list's heading, small
// print, a warning, or body text. A list row's own title is body text.
export function labelRole(classes) {
    const names = classes.join(' ');
    if (/list-item-title-description/.test(names)) return 'meta';
    if (/list-item-title/.test(names)) return 'body';
    if (/list-title/.test(names)) return 'heading';
    if (/(^|[-\s])(title|headline)([-\s]|$)/.test(names)) return 'title';
    if (/(error|warning)/.test(names)) return 'warning';
    if (/(fact-key|caption|hint|meta)/.test(names)) return 'meta';
    return 'body';
}

// How Back leaves a dialog: the button bound to Escape (Cancel, Close, Deny),
// else the only button there is (a dialog with one button is dismissed by it).
// `buttons` are {escape, reactive} in the dialog's order. Null: Back has
// nothing to do here.
export function backButton(buttons) {
    const reactive = buttons.filter(button => button.reactive);
    const escape = reactive.find(button => button.escape);
    if (escape) return escape;
    return reactive.length === 1 && buttons.length === 1 ? reactive[0] : null;
}

// The panel's width: the TV sheet's, inside the safe area.
// In whole pixels: at a unit that is not whole (1280x800) a fractional width came back from
// Clutter as a float32 that never equalled it, and restyle and placement requeued each other.
export function panelWidth(monitorWidth, unit, tokens) {
    return Math.round(Math.min(tokens.sheet.width * unit, monitorWidth - 2 * tokens.layout.safe_x * unit));
}

// The width a card's own placement gave it (the administrator prompt sizes its holder for the
// desktop), in whole pixels, or null when it already has the sheet's width (within half a pixel).
// The card is restyled once for each such width: a width it was set to by the sheet itself, read
// back as a float32, is never a change.
export function foreignWidth(moverWidth, width) {
    return Math.abs(moverWidth - width) >= 0.5 ? Math.round(moverWidth) : null;
}

// An actor property is set only when it really changes: lengths within a hundredth of a pixel
// are the same (Clutter keeps them as float32), anything else must be equal.
export function sameValue(current, value) {
    if (typeof current === 'number' && typeof value === 'number') return Math.abs(current - value) < 0.01;
    return current === value;
}

// The TV keyboard's height, from its own tokens: a key cell is a cap plus
// half the gaps around it, five rows.
export function keyboardHeight(keyboard) {
    return keyboard.padding_top + 5 * keyboard.key_height + 4 * keyboard.row_gap + keyboard.padding_bottom;
}

// How far the panel rises (a negative translation, or 0) so the TV keyboard
// never covers it: its bottom a gap above the keyboard, its top never above
// the safe line. Positions in stage pixels; keyboardTop null while the
// keyboard is away.
export function panelShift({top, bottom, keyboardTop, safeTop, gap}) {
    if (keyboardTop === null || bottom + gap <= keyboardTop) return 0;
    return Math.min(0, Math.max(keyboardTop - gap - bottom, safeTop - top));
}
