// Ranking suite for Luma Search's files, folders and apps.
// node tests/search/ranking.mjs path/to/lumaSearchRanking.js
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {pathToFileURL, fileURLToPath} from 'node:url';

const modulePath = process.argv[2];
const R = await import(pathToFileURL(modulePath));
const vectors = JSON.parse(fs.readFileSync(
    new URL('./ranking-vectors.json', import.meta.url), 'utf8'));
const context = {home: vectors.home, now: vectors.now, xdgFolders: new Set(vectors.xdg)};
const settings = JSON.parse(fs.readFileSync(new URL('./settings-cases.json', import.meta.url), 'utf8'));
const pages = JSON.parse(fs.readFileSync(process.argv.find(a => a.endsWith('settings-pages.json')) ??
    new URL('../../src/luma-search/settings-pages.json', import.meta.url), 'utf8')).pages;

// --dump prints every score so the Python twin can be compared exactly.
if (process.argv.includes('--dump')) {
    const out = {};
    for (const c of vectors.cases) {
        const terms = R.splitTerms(c.query);
        out[c.query] = R.rankFiles(vectors.files, terms, context, 50).map(f => [f.path, f.score]);
        out[c.query].push(...vectors.apps.map(a => [`app:${a.id}`, R.scoreApp(a, terms)]));
    }
    for (const c of settings.cases)
        out[`settings:${c.query}`] = R.rankSettingPages(pages, R.splitTerms(c.query)).map(i => [i.page.id, i.score]);
    const sorted = Object.fromEntries(Object.keys(out).sort().map(k => [k, out[k]]));
    console.log(JSON.stringify(sorted));
    process.exit(0);
}

let checked = 0;
for (const c of vectors.cases) {
    const terms = R.splitTerms(c.query);
    const files = R.rankFiles(vectors.files, terms, context, 50);
    const paths = files.map(f => f.path);
    const why = () => `${c.query}: ${files.map(f => `${f.path}=${f.score}[${f.evidence.join(',')}]`).join(' | ')}`;
    if (c.files_first) {
        assert.deepEqual(paths.slice(0, c.files_first.length), c.files_first, why());
        checked++;
    }
    for (const absent of c.files_absent ?? []) {
        assert.ok(!paths.includes(absent), why());
        checked++;
    }
    for (const [a, b] of c.files_before ?? []) {
        assert.ok(paths.includes(a), why());
        if (paths.includes(b))
            assert.ok(paths.indexOf(a) < paths.indexOf(b), `${a} before ${b}; ${why()}`);
        checked++;
    }
    if (c.top) {
        const entries = files.map((f, index) => ({key: `file:${f.path}`, score: f.score, kind: 'file', index}));
        vectors.apps.forEach((app, index) => {
            const score = R.scoreApp(app, terms);
            if (score !== null)
                entries.push({key: `app:${app.id}`, score, kind: 'app', index});
        });
        entries.sort(R.compareRanked);
        assert.equal(entries[0]?.key, c.top, `${c.query}: ${JSON.stringify(entries.slice(0, 4))}`);
        checked++;
    }
}

// Settings pages and options.
for (const c of settings.cases) {
    const ranked = R.rankSettingPages(pages, R.splitTerms(c.query));
    assert.equal(ranked[0]?.page.id ?? null, c.first, `${c.query}: ${JSON.stringify(ranked.map(i => [i.page.id, i.score]))}`);
    checked++;
}
for (const n of settings.names) {
    assert.equal(R.matchName(n.name, R.splitTerms(n.query))?.tier, n.tier, `${n.name} / ${n.query}`);
    checked++;
}

// Unit checks of the pieces.
assert.equal(R.foldText('Ångström Café'), 'angstrom cafe');
assert.deepEqual(R.nameWords('MyDocuments_v2-final'), ['my', 'documents', 'v', '2', 'final']);
assert.equal(R.matchName('report.pdf', ['report'], {ignoreExtension: true}).tier, R.TIER_EXACT);
assert.equal(R.matchName('Documents', ['docu']).tier, R.TIER_PREFIX);
assert.equal(R.matchName('Field Notes', ['notes']).tier, R.TIER_WORD);
assert.equal(R.matchName('Documents', ['cument']).tier, R.TIER_SUBSTRING);
assert.equal(R.matchName('Documents', ['documnets']).tier, R.TIER_FUZZY);
assert.equal(R.matchName('Documents', ['doc', 'x']), null);
assert.equal(R.matchName('Dots', ['dost']), null, 'short terms are never fuzzy');
assert.equal(R.junkPenalty('depot').penalty, 0);
assert.ok(R.junkPenalty('depot-1840284-backup-x2').penalty > 0);
assert.equal(R.junkPenalty('backup', ['backup']).penalty, 0, 'typed words are not penalised');
assert.ok(R.junkPenalty('IMG_20240512_101010.jpg').reasons.includes('date'));
assert.equal(R.ftsQuery(['depot-18', 'Backup']), 'depot* 18* Backup*');
assert.equal(R.ftsQuery(['documnets'], {shorten: true}), 'docu*');
assert.ok(R.scoreProviderResult('app', '4', ['2+2'], 0, 'org.gnome.Calculator.desktop') > 1300);
assert.ok(R.scoreProviderResult('mail', 'About the Viola release', ['about'], 0, 'org.projectluma.Charlie.desktop') <
    R.scoreSettingPage({title: 'About This Computer', path: 'System', keywords: ['about']}, ['about']),
'mail follows settings');
checked += 16;

// Performance: rank 100,000 candidates well inside a frame budget.
const big = [];
for (let i = 0; i < 100000; i++) {
    const dir = `/home/ada/Projects/p${i % 97}/src/m${i % 13}`;
    const name = i % 7 === 0 ? `depot-${i}-backup.tar` : i % 5 === 0 ? `Report ${i}.pdf` : `note-${i}.md`;
    big.push({path: `${dir}/${name}`, name, modified: vectors.now - i * 60});
}
big.push({path: '/home/ada/depot', name: 'depot'});
const started = performance.now();
const top = R.rankFiles(big, ['depot'], context, 20);
const elapsed = performance.now() - started;
assert.equal(top[0].path, '/home/ada/depot');
// The Shell ranks at most a few hundred index hits per keystroke; 100k is the
// stress case, so it gets a loose bound.
assert.ok(elapsed < 1500, `ranking 100k candidates took ${elapsed.toFixed(0)} ms`);
const small = big.slice(0, 400);
const t0 = performance.now();
for (let i = 0; i < 50; i++)
    R.rankFiles(small, ['depot'], context, 20);
const perQuery = (performance.now() - t0) / 50;
assert.ok(perQuery < 10, `ranking 400 hits took ${perQuery.toFixed(2)} ms`);
checked += 3;
console.log(`ranking: ${checked} checks passed; 100k candidates ${elapsed.toFixed(0)} ms; 400 hits ${perQuery.toFixed(2)} ms/query`);
