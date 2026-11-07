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
assert(M.labelRole(['dialog-list-title']) === 'heading', "a list's heading");
assert(M.labelRole(['dialog-list-item-title']) === 'body', "a list row's title");
assert(M.labelRole(['dialog-list-item-title-description']) === 'meta', "a list row's description");

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

// An administrator prompt at 1280x800 (ARM VM, 5 Nov): a unit that is not whole gave a
// fractional width; Clutter keeps lengths as float32, so the card's width never equalled it and
// restyle and placement requeued each other forever. Whole pixels, and one restyle per width the
// dialog's own placement gives it.
{
    const unit = Math.min(1280 / 1920, 800 / 1080);
    const width = M.panelWidth(1280, unit, tokens);
    assert(Number.isInteger(width) && width === Math.fround(width), 'the sheet width is whole pixels at 1280x800');
    // The loop as the shell runs it: a placement reads the card's width back as float32.
    const record = {foreignWidth: undefined};
    let mover = Math.fround(452.5);   // the prompt sized its holder for the desktop
    let restyles = 0;
    const place = () => {
        const foreign = M.foreignWidth(mover, width);
        if (foreign !== null && foreign !== record.foreignWidth) {
            record.foreignWidth = foreign;
            restyles++;
        }
        if (!M.sameValue(mover, width)) mover = Math.fround(width);
    };
    for (let pass = 0; pass < 50; pass++) place();
    assert(restyles === 1, `placement converges: one restyle, then none (${restyles})`);
    assert(M.sameValue(mover, width), 'the card keeps the sheet width');
    mover = Math.fround(452.5);       // the prompt places itself again at the same width
    place();
    assert(restyles === 1, 'the same width of its own is not restyled again');
    assert(M.foreignWidth(Math.fround(width + 0.3), width) === null, 'a float32 read-back is never a change');
    assert(M.sameValue(Math.fround(573.3333), 573.3333) && !M.sameValue(10, 11) && M.sameValue('a', 'a'),
        'a property is set only when it really changes');
}
assert(/foreign !== null && foreign !== record\.foreignWidth\) \{\s*record\.foreignWidth = foreign;\s*this\._queueRestyle\(record\);/.test(dialogs),
    'the shell restyles once per width of its own');
assert(/if \(!sameValue\(actor\[key\], value\)\) actor\[key\] = value;/.test(dialogs), 'and sets only real changes');
assert(!/mover\.width !== width/.test(dialogs), 'no exact float comparison requeues a restyle');

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
assert(/_safeFocus\(record\) \{[\s\S]*?if \(record\.fields\.some\([\s\S]*?_cancelButton\(record\)[\s\S]*?cancel\.grab_key_focus\(\)/.test(dialogs),
    'a dialog with nothing to type opens on Cancel');
assert(/_adopt\(dialog\) \{[\s\S]*?this\._safeFocus\(record\);[\s\S]*?idle_add[\s\S]*?this\._safeFocus\(record\)/.test(dialogs),
    'also after the dialog places its own focus');
assert(/const w = this\._px\(width \/ this\._u - 2 \* sheet\.padding - 3\)/.test(dialogs),
    'the card width: TV units scaled once, padding and rim inside the sheet width');
assert(/_placeMover\(record, monitor, width\) \{[\s\S]*?fixed_position_set[\s\S]*?'width', width/.test(dialogs) &&
    /if \(this\._placeMover\(record, monitor,/.test(dialogs), 'a card its dialog places itself takes the sheet width');
assert(/record\.containers\.add\(actor\);\s*actor\.connectObject\('child-added'/.test(dialogs) &&
    /for \(const actor of record\.containers\) actor\.disconnectObject\(this\)/.test(dialogs), 'rows added later are styled');
assert(/_scaleFixed\(record, actor\) \{[\s\S]*?node\.get_width\(\)[\s\S]*?value \* 1\.5/.test(dialogs) &&
    /if \(this\._scaleFixed\(record, actor\)\)/.test(dialogs), 'glyphs and discs a stylesheet sizes grow as icons do');
assert(/function textButton\(actor\) \{[\s\S]*?child instanceof St\.Label/.test(dialogs), 'a button with a word in it is an action');
assert(/_labelFields\(record\) \{[\s\S]*?actor instanceof St\.Button && actor\.label && actor\.visible/.test(dialogs),
    "the keyboard's action key names a button's own label");
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
