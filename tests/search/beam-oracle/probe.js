// Beam search oracle: types queries into Luma Search in a headless Shell,
// records the merged result rows, first-result latency and a Ctrl+Enter
// reveal, and crops screenshots. Output in $ORACLE_OUT.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OUT = GLib.getenv('ORACLE_OUT') ?? '/tmp/beam-oracle';
const QUERIES = (GLib.getenv('ORACLE_QUERIES') ?? 'Docu,depot,report,default,dark,sleep,wifi,about,viola').split(',');
const SCALE = Number(GLib.getenv('ORACLE_SCALE') || 1);

function dbusCall(method, params) {
    return new Promise((resolve, reject) => Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig',
        '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig', method, params, null, 0, -1, null,
        (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
}

// Apply a fractional scale such as 1.25 to the virtual monitor.
async function applyScale(scale) {
    const [serial, monitors] = (await dbusCall('GetCurrentState', null)).deepUnpack();
    const [[connector], modes] = monitors[0];
    const mode = modes.find(m => m[6]?.['is-current']?.deepUnpack?.()) ?? modes[0];
    const supported = mode[5];
    const chosen = supported.reduce((a, b) => Math.abs(b - scale) < Math.abs(a - scale) ? b : a, supported[0]);
    await dbusCall('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
        [serial, 1, [[0, 0, chosen, 0, true, [[connector, mode[0], {}]]]], {}]));
    return chosen;
}

// Every visible row's kind label must be whole and clear of the scrollbar.
function labelChecks(dialog) {
    const scroll = dialog._results._scrollView;
    const [sx] = scroll.get_transformed_position();
    const [sw] = scroll.get_transformed_size();
    const bar = scroll.get_children().find(c => c.constructor.name.includes('ScrollBar') && c.vertical && c.visible);
    const barLeft = bar ? bar.get_transformed_position()[0] : sx + sw;
    const problems = [];
    for (const row of dialog._results._merged.get_children().filter(r => r.visible)) {
        const label = row._kindLabel;
        if (!label?.mapped || label.opacity === 0)
            continue;
        const [lx] = label.get_transformed_position();
        const [lw] = label.get_transformed_size();
        const rowW = row.get_transformed_size()[0];
        const [rx] = row.get_transformed_position();
        if (label.clutter_text.get_layout().is_ellipsized())
            problems.push(`${row.metaInfo.name}: label ellipsized`);
        if (lx + lw > Math.min(rx + rowW, barLeft) + 0.5)
            problems.push(`${row.metaInfo.name}: label past the row or under the scrollbar`);
    }
    return {scrollbar: Boolean(bar), problems};
}
Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');
const log = (m, o) => console.log(`[beam-search] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));

async function shot(name, actor) {
    const [x, y] = actor.get_transformed_position();
    const [w, h] = actor.get_transformed_size();
    const shooter = new Shell.Screenshot();
    const [content, scale] = await shooter.screenshot_stage_to_content();
    const file = Gio.File.new_for_path(`${OUT}/${name}.png`);
    const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
    await Shell.Screenshot.composite_to_stream(content.get_texture(), Math.max(0, x - 24), Math.max(0, y - 24),
        w + 48, h + 48, scale, null, 0, 0, 1, stream);
    stream.close(null);
}

function rows(dialog) {
    const merged = dialog._results._merged;
    return merged.get_children().filter(r => r.visible).map(r => ({
        title: r.metaInfo.name,
        detail: r.metaInfo.description ?? '',
        kind: r._kindLabel?.text ?? '',
        score: r._lumaRank?.score,
        provider: r.provider.id,
    }));
}

async function work() {
    GLib.mkdir_with_parents(OUT, 0o755);
    const search = Main.lumaSearch;
    const dialog = search._dialog;
    const summary = {queries: {}, errors: [], scale: null};
    const summaryScale = SCALE !== 1 ? await applyScale(SCALE) : 1;
    if (SCALE !== 1)
        await sleep(4000);
    search.open();
    await sleep(1500);
    await shot('beam-empty', dialog._island);
    for (const query of QUERIES) {
        dialog._entry.text = '';
        await sleep(300);
        const started = GLib.get_monotonic_time();
        dialog._entry.text = query;
        let first = null;
        for (let i = 0; i < 400 && first === null; i++) {
            if (dialog._results._merged.get_children().some(r => r.visible))
                first = (GLib.get_monotonic_time() - started) / 1000;
            else
                await sleep(2);
        }
        await sleep(900);
        const result = {
            firstResultMs: first,
            rows: rows(dialog),
            labels: labelChecks(dialog),
            providers: Object.fromEntries(dialog._results._providers.map(p => [p.id, (dialog._results._results[p.id] ?? []).length])),
        };
        summary.queries[query] = result;
        log('query', {query, first, top: result.rows.slice(0, 8).map(r => `${r.title} (${r.kind})`), labels: result.labels, providers: result.providers});
        await shot(`beam-${query}`, dialog._island);
    }
    // Ctrl+Enter shows the default result in Filer (recorded by a stand-in
    // FileManager1 service).
    dialog._entry.text = 'depot';
    await sleep(1200);
    const selected = dialog._results._defaultResult;
    summary.revealTarget = selected?.metaInfo?.id ?? null;
    selected?.setLumaSelected?.(true);
    await sleep(300);
    await shot('beam-depot-selected', dialog._island);
    dialog._results.revealDefault();
    await sleep(800);
    summary.dialogStateAfterReveal = dialog.state;
    // Enter on "sleep" opens Power in Settings (recorded by the stand-in).
    search.open();
    await sleep(800);
    dialog._entry.text = 'sleep';
    await sleep(1200);
    summary.sleepDefault = dialog._results._defaultResult?.metaInfo?.name ?? null;
    dialog._results.activateDefault();
    await sleep(800);
    summary.scale = summaryScale;
    summary.labelProblems = Object.values(summary.queries).flatMap(q => q.labels.problems);
    GLib.file_set_contents(`${OUT}/summary.json`, JSON.stringify(summary, null, 1));
    log('done', {revealTarget: summary.revealTarget, state: dialog.state});
}

let started = false;
export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, Number(GLib.getenv('ORACLE_SETTLE') ?? 9000), () => {
        if (!started) {
            started = true;
            work().catch(e => console.error(`[beam-search] ${e}\n${e.stack}`)).finally(() => global.context.terminate());
        }
        return GLib.SOURCE_REMOVE;
    });
}

export async function run() {
    await new Promise(() => {});
}
