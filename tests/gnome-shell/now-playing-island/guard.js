// Now-playing island guard (Shell patch 0145): injected faults are logged once
// per minute and put right; interrupted animations settle; nothing is logged
// in normal use.
import GLib from 'gi://GLib';
import St from 'gi://St';

const log = (m, o) => console.log(`[guard] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));

export default async function ({Main, shot}) {
    const shelf = Main.shelf;
    const tag = GLib.getenv('B_TAG');
    for (let i = 0; i < 120 && !shelf?._media?.eligible; i++) await sleep(500);
    const media = shelf._media, island = shelf._mediaIsland, guard = shelf._mediaGuard;
    const results = [];
    const record = (name, pass, evidence) => { results.push(pass); log(pass ? 'PASS' : 'FAIL', {name, ...evidence}); };
    if (!guard) { record('guard present', false, {}); return; }
    await sleep(3000);
    const vertical = shelf._vertical;
    const size = () => Math.round(vertical ? island.height : island.width);
    const natural = () => Math.round((vertical ? island.get_preferred_height(island.width) : island.get_preferred_width(island.height))[1]);
    const whole = () => island.opacity === 255 && size() >= natural() - 1 && !(vertical ? island.natural_height_set : island.natural_width_set) &&
        media._controls.visible && media._copy.opacity === 255 && media.opacity === 255;
    const state = () => ({opacity: island.opacity, size: size(), natural: natural(), copy: media._copy.opacity, controls: media._controls.visible, snaps: guard.snaps});
    // Headless start-up leaves animations inhibited; the person's setting decides.
    if (GLib.getenv('B_ANIM') !== 'false' && !St.Settings.get().enable_animations)
        St.Settings.get().uninhibit_animations();
    const reduced = !St.Settings.get().enable_animations;
    log('settings', {enable: St.Settings.get().enable_animations, gsettings: new (imports.gi.Gio.Settings)({schema_id: 'org.gnome.desktop.interface'}).get_boolean('enable-animations'), slow: St.Settings.get().slow_down_factor});
    const snaps0 = guard.snaps;
    await sleep(Number(GLib.getenv('G_QUIET') || 4000));
    record('normal use: whole and nothing restored', whole() && guard.snaps === snaps0, state());
    const r = island.get_transformed_extents();
    const k = St.ThemeContext.get_for_stage(global.stage).scale_factor;
    await shot(`island-before-${tag}`, (r.get_x() - 12) * k, (r.get_y() - 12) * k, (r.get_width() + 24) * k, (r.get_height() + 24) * k);

    // A show cut short by something outside the island.
    media.eligible = false; media.emit('eligibility-changed');
    await sleep(400);
    media.eligible = true; media.emit('eligibility-changed');
    await sleep(70);
    island.remove_all_transitions();
    await sleep(300);
    record('a show stopped from outside settles opaque at natural size without the guard', whole() && guard.snaps === snaps0,
        {...state(), last: guard._last});

    // A hide interrupted by a show, reversed quickly.
    media.eligible = false; media.emit('eligibility-changed');
    await sleep(90);
    media.eligible = true; media.emit('eligibility-changed');
    await sleep(700);
    record('hide then show quickly ends whole', whole() && guard.snaps === snaps0, state());

    // A title crossfade stopped from outside.
    media._crossfade(media._copy, 'title', 'probe');
    await sleep(50);
    media._copy.remove_all_transitions();
    await sleep(50);
    record('a stopped title crossfade leaves the copy opaque', media._copy.opacity === 255,
        {copy: media._copy.opacity, lastCrossfade: media._lastMotion});

    // Injected faults: the island left translucent, held narrow, controls gone.
    const faults = {
        translucent: () => { island.opacity = 140; },
        narrow: () => { island[vertical ? 'height' : 'width'] = Math.round(natural() * 0.66); },
        'controls hidden': () => media._controls.hide(),
        'copy translucent': () => { media._copy.opacity = 90; },
    };
    let n = guard.snaps;
    const waitSnap = async count => {
        const t0 = GLib.get_monotonic_time();
        while (guard.snaps < count && GLib.get_monotonic_time() - t0 < 4e6) await sleep(50);
        return Math.round((GLib.get_monotonic_time() - t0) / 1000);
    };
    for (const [name, inject] of Object.entries(faults)) {
        inject();
        const t0 = GLib.get_monotonic_time();
        const took = await waitSnap(n + 1);
        await sleep(300);
        record(`fault "${name}" is put right after about a second`, took >= 900 && took <= 2600 && whole() && guard.snaps === n + 1,
            {...state(), took});
        n = guard.snaps;
        if (name === 'translucent')
            await shot(`island-after-${tag}`, (r.get_x() - 12) * k, (r.get_y() - 12) * k, (r.get_width() + 24) * k, (r.get_height() + 24) * k);
        await sleep(1500);
    }
    record('reported once per minute: later faults counted, not logged', guard._suppressed === Object.keys(faults).length - 1,
        {suppressed: guard._suppressed});
    // Leave the island broken while animations run: the guard waits for them.
    island.opacity = 150;
    island.ease({opacity: 149, duration: 1500 / St.Settings.get().slow_down_factor});
    await sleep(1300);
    record('the guard does not act during an animation', guard.snaps === n, state());
    await sleep(3000);
    record('and acts after it', whole() && guard.snaps === n + 1, state());
    // Everyday churn must never trip it: players coming and going, Capture,
    // the overview, the shelf hidden and shown mid-animation.
    await sleep(3000);
    const before = guard.snaps;
    let root = island; while (root.get_parent() && root.get_parent() !== Main.layoutManager.uiGroup) root = root.get_parent();
    const flip = v => { media.eligible = v; media.emit('eligibility-changed'); };
    for (const gap of [40, 120, 190, 260, 600]) { flip(false); await sleep(gap); flip(true); await sleep(900); }
    flip(false); await sleep(80); root.hide(); await sleep(300); flip(true); await sleep(50); root.show(); await sleep(1200);
    try { Main.screenshotUI.open(); await sleep(1500); Main.screenshotUI.close(); } catch (e) { log('capture', {e: `${e}`}); }
    await sleep(1200);
    Main.overview.show(); await sleep(1500); Main.overview.hide(); await sleep(2500);
    record('everyday churn: whole, and the guard never needed', whole() && guard.snaps === before, state());
    log('summary', {passed: results.filter(Boolean).length, total: results.length, reduced});
}
