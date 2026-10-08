// Beam's file index against a real LocalSearch that has crawled a synthetic
// home. Run by tests/search/localsearch-harness.sh inside a D-Bus session.
// gjs -m tests/search/file-index-gjs.js path/to/js/ui HOME
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import System from 'system';

globalThis._ = s => s;
const [uiDir, home] = ARGV;
const {LumaFileIndex} = await import(Gio.File.new_for_path(`${uiDir}/lumaFileIndex.js`).get_uri());

let failures = 0;
const check = (ok, message) => {
    print(`${ok ? 'ok  ' : 'FAIL'} ${message}`);
    if (!ok)
        failures++;
};
const xdg = ['Desktop', 'Documents', 'Downloads', 'Music', 'Pictures', 'Videos'].map(n => `${home}/${n}`);
const makeIndex = (privacy = null) => new LumaFileIndex({
    home,
    xdgFolders: xdg,
    recentPath: `${home}/.local/share/recently-used.xbel`,
    picksPath: `${home}/.local/state/luma/search-picks.json`,
    privacySettings: privacy,
});
const index = makeIndex();
const names = items => items.map(item => item.path.slice(home.length + 1));

// Warm the connection the way Beam does when it opens.
index.warmUp();
await index.search(['warm']);

const expectFirst = async (query, expected, extra = () => true) => {
    const started = GLib.get_monotonic_time();
    const items = await index.search(query.split(' '));
    const ms = (GLib.get_monotonic_time() - started) / 1000;
    const got = names(items);
    check(got[0] === expected && extra(got), `"${query}" -> ${got.slice(0, 5).join(' | ')} (${ms.toFixed(1)} ms)`);
    return got;
};

await expectFirst('Docu', 'Documents');
await expectFirst('documnets', 'Documents');
const before = (got, a, b) => !got.includes(b) || got.indexOf(a) < got.indexOf(b);
await expectFirst('depot', 'depot', got =>
    before(got, 'depot', 'depot-1840284-backup-x2') && before(got, 'depot', 'depot-old') &&
    !got.some(n => n.includes('node_modules') || n.includes('/.') || n.startsWith('.')));
await expectFirst('report', 'Documents/report.pdf', got =>
    before(got, 'Documents/report.pdf', 'Documents/report (copy).pdf') &&
    before(got, 'Documents/report.pdf', 'Documents/report (1).pdf'));
await expectFirst('budget', 'Documents/budget.ods', got =>
    before(got, 'Documents/budget.ods', 'Documents/Work/2025/Q3/archive/drafts/budget.ods'));
await expectFirst('invoice', 'Documents/Taxes/invoice-march.pdf');
await expectFirst('cafe', 'Pictures/Café Menu.png');
await expectFirst('depot backup', 'depot-1840284-backup-x2');

// Privacy: without recent files the recently opened invoice loses its lead.
const privacy = {get_boolean: () => false, get_int: () => -1};
const privateIndex = makeIndex(privacy);
const privateItems = names(await privateIndex.search(['invoice']));
check(privateItems[0] === 'Documents/Taxes/invoice-april.pdf',
    `remember-recent-files off ignores history -> ${privateItems.slice(0, 2).join(' | ')}`);

// A pick raises a result next time.
const beforePick = names(await index.search(['invoice']));
const picked = beforePick.at(-1);
index.recordPick(`${home}/${picked}`);
index.recordPick(`${home}/${picked}`);
const afterPick = names(await index.search(['invoice']));
check(afterPick.indexOf(picked) >= 0 && afterPick.indexOf(picked) < beforePick.indexOf(picked),
    `picking ${picked} twice raises it from ${beforePick.indexOf(picked)} to ${afterPick.indexOf(picked)}`);

// Cancellation: a superseded keystroke is abandoned.
const cancellable = new Gio.Cancellable();
const pending = index.search(['note'], cancellable);
cancellable.cancel();
try {
    await pending;
    check(false, 'cancelled search rejects');
} catch (e) {
    check(e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED), `cancelled search rejects (${e.message})`);
}

// Latency across a typing session over the whole index.
const queries = ['d', 'de', 'dep', 'depo', 'depot', 'n', 'no', 'not', 'note', 'notes', 'r', 're', 'rep',
    'repo', 'report', 'q', 'qu', 'qua', 'quar', 'quarterly', 'p', 'ph', 'pho', 'phot', 'photo', 'x', 'xy', 'zz'];
const times = [];
for (let round = 0; round < 3; round++) {
    for (const q of queries) {
        const started = GLib.get_monotonic_time();
        // eslint-disable-next-line no-await-in-loop
        await index.search([q]);
        times.push((GLib.get_monotonic_time() - started) / 1000);
    }
}
times.sort((a, b) => a - b);
const p50 = times[Math.floor(times.length * 0.5)];
const p95 = times[Math.floor(times.length * 0.95)];
check(p95 < 50, `typing latency over the index: p50 ${p50.toFixed(1)} ms, p95 ${p95.toFixed(1)} ms, max ${times.at(-1).toFixed(1)} ms`);

index.destroy();
print(failures ? `file index: FAIL (${failures})` : 'file index: PASS');
System.exit(failures ? 1 : 0);
