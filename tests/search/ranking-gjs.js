// The packaged Beam ranking module, run by gjs (the Shell's engine).
// gjs -m tests/search/ranking-gjs.js lumaSearchRanking.js ranking-vectors.json
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import System from 'system';

const [modulePath, vectorsPath, settingsCasesPath, pagesPath] = ARGV;
const R = await import(Gio.File.new_for_path(modulePath).get_uri());
const [, bytes] = Gio.File.new_for_path(vectorsPath).load_contents(null);
const vectors = JSON.parse(new TextDecoder().decode(bytes));
const readJson = path => JSON.parse(new TextDecoder().decode(Gio.File.new_for_path(path).load_contents(null)[1]));
const context = {home: vectors.home, now: vectors.now, xdgFolders: new Set(vectors.xdg)};

let failures = 0;
let checked = 0;
const check = (ok, message) => {
    checked++;
    if (!ok) {
        failures++;
        printerr(`FAIL: ${message}`);
    }
};

for (const c of vectors.cases) {
    const terms = R.splitTerms(c.query);
    const files = R.rankFiles(vectors.files, terms, context, 50);
    const paths = files.map(f => f.path);
    const why = `${c.query}: ${files.map(f => `${f.path}=${f.score}`).join(' | ')}`;
    if (c.files_first)
        check(JSON.stringify(paths.slice(0, c.files_first.length)) === JSON.stringify(c.files_first), why);
    for (const absent of c.files_absent ?? [])
        check(!paths.includes(absent), why);
    for (const [a, b] of c.files_before ?? [])
        check(paths.includes(a) && (!paths.includes(b) || paths.indexOf(a) < paths.indexOf(b)), `${a} before ${b}; ${why}`);
    if (c.top) {
        const entries = files.map((f, index) => ({key: `file:${f.path}`, score: f.score, kind: 'file', index}));
        vectors.apps.forEach((app, index) => {
            const score = R.scoreApp(app, terms);
            if (score !== null)
                entries.push({key: `app:${app.id}`, score, kind: 'app', index});
        });
        entries.sort(R.compareRanked);
        check(entries[0]?.key === c.top, `${c.query}: top ${entries[0]?.key}`);
    }
}
if (settingsCasesPath && pagesPath) {
    const settings = readJson(settingsCasesPath);
    const pages = readJson(pagesPath).pages;
    for (const c of settings.cases) {
        const ranked = R.rankSettingPages(pages, R.splitTerms(c.query));
        check((ranked[0]?.page.id ?? null) === c.first, `settings "${c.query}" -> ${ranked[0]?.page.id}`);
    }
    for (const n of settings.names)
        check(R.matchName(n.name, R.splitTerms(n.query))?.tier === n.tier, `${n.name} / ${n.query}`);
}
check(R.foldText('Ångström Café') === 'angstrom cafe', 'accents fold under SpiderMonkey');
check(R.matchName('Documents', ['documnets'])?.tier === R.TIER_FUZZY, 'typo tolerance');

const big = [];
for (let i = 0; i < 100000; i++) {
    const name = i % 7 === 0 ? `depot-${i}-backup.tar` : `note-${i}.md`;
    big.push({path: `/home/ada/Projects/p${i % 97}/${name}`, name});
}
big.push({path: '/home/ada/depot', name: 'depot'});
const started = GLib.get_monotonic_time();
const top = R.rankFiles(big, ['depot'], context, 20);
const ms = (GLib.get_monotonic_time() - started) / 1000;
check(top[0]?.path === '/home/ada/depot', '100k: clean folder first');
print(`ranking (gjs): ${checked - failures}/${checked} checks; 100k candidates ${ms.toFixed(0)} ms`);
if (failures) {
    print('ranking (gjs): FAIL');
    System.exit(1);
}
print('ranking (gjs): PASS');
