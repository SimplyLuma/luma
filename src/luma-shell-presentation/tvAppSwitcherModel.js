// SPDX-License-Identifier: Apache-2.0
// The TV app switcher's rules (no GI imports, so tests run them under node or
// gjs). Lengths are TV units (tvTokens.js); times are milliseconds.

// Home pressed twice: a short press waits `windowMs` for a second one before
// it goes home. A second press inside the window opens the switcher.
// `pendingSince` is when the first press was let go (null: none waiting).
export function homeSecondPress(pendingSince, now, windowMs) {
    return pendingSince !== null && now - pendingSince <= windowMs;
}

// The switcher's apps, newest first: the apps of the windows in the order the
// desktop's Alt+Tab lists them (`windows`: [{app}] most recent first), then
// any other running app (`running`: [app]) that has no window to show. Each
// app once; null apps (windows no app owns) are left out.
export function switcherApps(windows, running, id = app => app) {
    const seen = new Set();
    const apps = [];
    const add = app => {
        if (!app || seen.has(id(app))) return;
        seen.add(id(app));
        apps.push(app);
    };
    for (const window of windows) add(window.app);
    for (const app of running) add(app);
    return apps;
}

// Whether an app may be a card or carry a running dot: the shell's own
// classification, as its app grid has it (`appInfo.should_show()`: NoDisplay,
// Hidden, OnlyShowIn). System prompters (the keyring's Access Prompt and the
// like) are NoDisplay; a window no desktop file claims (`appInfo` null) is an
// ordinary app's.
export function shownApp(appInfo) {
    return !appInfo || appInfo.should_show?.() !== false;
}

// The row's scroll (TV units): the first card on the side safe line; the row
// moves only as far as the focused card and `peek` past it need, never
// before the first card.
export function rowScroll(index, tokens, screenWidth) {
    const {card_width: width, card_gap: gap, peek} = tokens.app_switcher;
    const safe = tokens.layout.safe_x;
    const end = safe + (index + 1) * width + index * gap + peek;
    return Math.max(0, end - screenWidth);
}

// A card's place in its close: 'open' (nothing asked), 'closing' (asked, still
// running, within the grace time), 'stuck' (still running after it).
export function closeState(requestedAt, now, graceMs) {
    if (requestedAt === null || requestedAt === undefined) return 'open';
    return now - requestedAt >= graceMs ? 'stuck' : 'closing';
}

// A card's menu: Close; once the app has had its grace time and is still
// there, Force Close and Wait. Force Close only when there is a window to
// end (the desktop's Force Quit acts on windows).
export function cardMenu(state, canForce = true) {
    if (state === 'stuck')
        return canForce ? ['force', 'wait'] : ['wait'];
    if (state === 'closing') return [];
    return ['close'];
}

// A TV home tile's menu (a held OK): Open and Close while the app runs;
// nothing (the hold is an ordinary OK) while it does not.
export function tileMenu(running) {
    return running ? ['open', 'close'] : [];
}

// OK on a card or a tile: a press let go before `holdMs` is OK; one held that
// long opens the item's menu once, and its release then does nothing.
export function okHold(downAt, upAt, holdMs) {
    return upAt - downAt >= holdMs ? 'menu' : 'ok';
}

// Left/Right along the row, kept inside it.
export function stepCard(index, count, direction) {
    if (!count) return 0;
    const next = index + (direction === 'right' ? 1 : direction === 'left' ? -1 : 0);
    return Math.max(0, Math.min(count - 1, next));
}

// The card focused after one leaves: the one that took its place, else the
// one before it.
export function afterRemoval(index, removed, count) {
    if (count <= 0) return 0;
    if (removed < index) return index - 1;
    return Math.min(index, count - 1);
}
