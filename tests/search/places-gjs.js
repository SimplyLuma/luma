// Beam offers Filer's places: the packaged Shell module, run by gjs.
// gjs -m tests/search/places-gjs.js path/to/lumaFilerPlaces.js path/to/filer-places.json
// lumaSearchRanking.js must sit next to lumaFilerPlaces.js, as in js/ui.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import System from 'system';

const [modulePath, tablePath] = ARGV;
const P = await import(Gio.File.new_for_path(modulePath).get_uri());
const R = await import(Gio.File.new_for_path(modulePath).get_parent()
    .get_child('lumaSearchRanking.js').get_uri());
const table = JSON.parse(new TextDecoder().decode(
    Gio.File.new_for_path(tablePath).load_contents(null)[1]));

let failures = 0;
let checked = 0;
const check = (ok, message) => {
    checked++;
    if (!ok) {
        failures++;
        printerr(`FAIL: ${message}`);
    }
};

const home = GLib.dir_make_tmp('luma-places-XXXXXX');
const specialDirs = {};
for (const [key, name] of [['DESKTOP', 'Desktop'], ['DOCUMENTS', 'Documents'],
    ['DOWNLOAD', 'Downloads'], ['PICTURES', 'Pictures'], ['VIDEOS', 'Videos']]) {
    specialDirs[key] = `${home}/${name}`;
    GLib.mkdir_with_parents(specialDirs[key], 0o700);
}
specialDirs.MUSIC = `${home}/Music`; // configured, but not there
const env = extra => ({home, specialDirs, bookmarks: '', ...extra});

const top = (query, extra = {}) => {
    const places = P.resolvePlaces(table, env(extra));
    const ranked = P.rankPlaces(places, R.splitTerms(query));
    return ranked[0]?.place ?? null;
};

check(table.places?.length >= 13, `the places table did not load (${table.places?.length})`);
check(top('Applications')?.uri === 'applications:///', `Applications: ${JSON.stringify(top('Applications'))}`);
check(top('apps')?.uri === 'applications:///', `apps: ${JSON.stringify(top('apps'))}`);
check(top('trash')?.uri === 'trash:///', `trash: ${JSON.stringify(top('trash'))}`);
check(top('bin')?.uri === 'trash:///', `bin: ${JSON.stringify(top('bin'))}`);
check(top('other locations')?.uri === 'x-network-view:///', 'other locations leads to Network');
check(top('documents')?.uri === GLib.filename_to_uri(specialDirs.DOCUMENTS, null), 'documents');
check(top('music') === null || top('music').id !== 'music', 'a missing Music folder is not offered');
check(top('recent', {rememberRecent: false})?.id !== 'recent', 'Recent hidden when recent files are off');
const spanish = {Trash: 'Papelera'};
check(top('papelera', {translate: t => spanish[t] ?? t})?.title === 'Papelera', 'translated label');
check(top('trash', {translate: t => spanish[t] ?? t})?.title === 'Papelera', 'English word, translated title');
const bookmarked = `${home}/Projects`;
GLib.mkdir_with_parents(bookmarked, 0o700);
check(top('work', {bookmarks: `${GLib.filename_to_uri(bookmarked, null)} Work\n`})?.uri ===
    GLib.filename_to_uri(bookmarked, null), 'bookmark by label');

for (const dir of [...Object.values(specialDirs), bookmarked])
    GLib.rmdir(dir);
GLib.rmdir(home);

print(`filer places: ${checked} checks, ${failures} failures`);
if (checked < 12 || failures)
    System.exit(1);
