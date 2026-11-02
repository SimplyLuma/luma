// SPDX-License-Identifier: GPL-2.0-or-later
// System dialogs in TV posture: the remote's keys in a dialog, Back, the
// panel above the TV keyboard, and the wiring that keeps a dialog's keys
// alive while it holds the grab.
// Run: gjs -m tv-dialogs-policy.js js/ui/lumaPresentation/tvDialogsModel.js \
//        js/ui/lumaPresentation/tvTokens.js js/ui/lumaPresentation/tvDialogs.js \
//        js/ui/lumaPresentation/tv.js js/ui/lumaPresentation/tvInput.js
// (node tv-dialogs-policy.js ... works too).
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
const dialogs = read(args[2]);
const home = read(args[3]);
const input = read(args[4]);

// The remote's keys: arrows, OK, Back (Escape or a remote's XF86Back).
assert(M.dialogKey('Escape') === 'back' && M.dialogKey('XF86Back') === 'back', 'Back');
assert(['Return', 'KP_Enter', 'ISO_Enter'].every(name => M.dialogKey(name) === 'ok'), 'OK');
assert(['Up', 'Down', 'Left', 'Right'].every(name => M.dialogKey(name) === name.toLowerCase()), 'arrows');
assert(M.dialogKey('a') === null && M.dialogKey('BackSpace') === null, 'typing is not the remote');

// Parts of a card by their classes.
assert(M.labelRole(['message-dialog-title']) === 'title', 'a dialog title');
assert(M.labelRole(['luma-seal-title']) === 'title', "the administrator prompt's title");
assert(M.labelRole(['headline']) === 'title', 'a headline');
assert(M.labelRole(['message-dialog-description']) === 'body', 'body text');
assert(M.labelRole(['prompt-dialog-error-label']) === 'warning', 'a warning');
assert(M.labelRole(['luma-seal-fact-key']) === 'meta', 'small print');
assert(M.labelRole([]) === 'body', 'an unclassed label is body text');

// Back: the Escape button, else a lone button; never a guess between two.
const cancel = {escape: true, reactive: true};
const ok = {escape: false, reactive: true};
assert(M.backButton([cancel, ok]) === cancel, 'Cancel');
assert(M.backButton([ok, cancel]) === cancel, 'wherever it sits');
assert(M.backButton([ok]) === ok, 'a one-button dialog is dismissed by it');
assert(M.backButton([ok, {...ok}]) === null, 'two buttons and no Cancel: nothing');
assert(M.backButton([{...cancel, reactive: false}, ok]) === null, 'an insensitive Cancel is not pressed');

// The panel: the TV sheet's width inside the safe area, at any size.
assert(M.panelWidth(1920, 1, tokens) === tokens.sheet.width, 'the sheet width at 1080p');
assert(M.panelWidth(3840, 2, tokens) === 2 * tokens.sheet.width, 'scaled at 4K');
assert(M.panelWidth(900, 1, tokens) === 900 - 2 * tokens.layout.safe_x, 'never past the safe area');

// Above the TV keyboard: the keyboard's own height; the panel rises only as
// far as it must and never above the safe line.
const kb = M.keyboardHeight(tokens.keyboard);
assert(kb === 428, `the TV keyboard is 428 tall (${kb})`);
const keyboardTop = 1080 - kb;
assert(M.panelShift({top: 300, bottom: 600, keyboardTop: null, safeTop: 56, gap: 24}) === 0, 'no keyboard: no move');
assert(M.panelShift({top: 100, bottom: 500, keyboardTop, safeTop: 56, gap: 24}) === 0, 'clear of it: no move');
assert(M.panelShift({top: 340, bottom: 740, keyboardTop, safeTop: 56, gap: 24}) === keyboardTop - 24 - 740,
    'just above it');
assert(M.panelShift({top: 200, bottom: 900, keyboardTop, safeTop: 56, gap: 24}) === 56 - 200,
    'a tall panel stops at the safe line');

// The adapter: only in TV posture, every ModalDialog, focus kept in the
// dialog that holds the grab, Back acting even without the focus.
assert(/_enter\(\) \{[\s\S]*?this\._dialogs = new TvDialogs\(/.test(home), 'TV posture adopts dialogs');
assert(/_leave\(\) \{[\s\S]*?this\._dialogs\?\.destroy\(\)/.test(home), 'leaving TV posture gives them back');
assert(/modalDialogGroup;[\s\S]*?connectObject\('child-added'/.test(dialogs), 'every ModalDialog');
assert(/'notify::key-focus'/.test(dialogs) && /get_grab_actor/.test(dialogs) && /_refocus\(record\)/.test(dialogs),
    'the focus comes back to the dialog with the grab');
assert(/'captured-event'/.test(dialogs) && /backButton\(buttons\)/.test(dialogs), 'Back in the capture phase');
assert(/effect instanceof Shell\.BlurEffect[\s\S]*?effect\.enabled = false/.test(dialogs), 'no live blur');
assert(/setFieldAction\?\.\(entry\.clutter_text/.test(dialogs), "the keyboard's action key is the dialog's");
assert(/for \(const effect of record\.blurs\) effect\.enabled = true/.test(dialogs) &&
    /for \(const \[actor, value\] of record\.saved\)/.test(dialogs), 'everything given back');

// The TV home never takes the focus from under a grab; the pad reaches a
// Shell grab wherever the stage focus is.
const grabs = home.match(/(?:this\._home|this\._entry)\.grab_key_focus\(\)/g) ?? [];
const guarded = home.match(/if \(!Main\.modalCount\) (?:this\._home|this\._entry)\.grab_key_focus\(\)/g) ?? [];
assert(grabs.length > 0 && grabs.length === guarded.length, 'the TV home waits for the grab to end');
assert(/shellFocus = \(Boolean\(focus\) && focus !== global\.stage\) \|\| Main\.modalCount > 0/.test(input),
    'a Shell grab gets the pad');

print('tv-dialogs-policy: ok');

function print(message) {
    if (gjs)
        globalThis.print(message);
    else
        console.log(message);
}
