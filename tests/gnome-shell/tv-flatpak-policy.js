// SPDX-License-Identifier: GPL-2.0-or-later
// The TV home and Flatpaks: Flatpak's export keeps only allowlisted desktop keys, so an app's
// X-Luma-TV* keys are read from its own deployed desktop file, inside the system's or the user's
// installation only, and its wide tile from the deploy's share/luma-tv. Checked on a fake deploy
// tree in a temporary folder.
// Run: gjs -m tv-flatpak-policy.js js/ui/lumaPresentation/tvModel.js js/ui/lumaPresentation/tv.js
// (node tv-flatpak-policy.js ... works too).
const gjs = typeof globalThis.ARGV !== 'undefined';
const args = gjs ? globalThis.ARGV : process.argv.slice(2);
const GLib = gjs ? (await import('gi://GLib')).default : null;
const fs = gjs ? null : await import('node:fs');
const os = gjs ? null : await import('node:os');
const cwd = gjs ? GLib.get_current_dir() : process.cwd();
const absolute = path => (path.startsWith('/') ? path : `${cwd}/${path}`);
const read = path => {
    try {
        return gjs ? new TextDecoder().decode(GLib.file_get_contents(path)[1]) : fs.readFileSync(path, 'utf8');
    } catch {
        return null;
    }
};
const write = (path, text) => {
    const folder = path.slice(0, path.lastIndexOf('/'));
    if (gjs) {
        GLib.mkdir_with_parents(folder, 0o755);
        GLib.file_set_contents(path, text);
    } else {
        fs.mkdirSync(folder, {recursive: true});
        fs.writeFileSync(path, text);
    }
};
const print = gjs ? globalThis.print : console.log;

function assert(value, message) {
    if (!value)
        throw new Error(`FAIL: ${message}`);
}

const M = await import(`file://${absolute(args[0])}`);
const home = read(absolute(args[1]));

// A fake system installation and a fake user one.
const top = gjs ? GLib.dir_make_tmp('luma-tv-flatpak-XXXXXX') : fs.mkdtempSync(`${os.tmpdir()}/luma-tv-flatpak-`);
const system = `${top}/var/lib/flatpak`, user = `${top}/home/.local/share/flatpak`;
const roots = [{exports: `${system}/exports/share/applications/`, deploy: `${system}/app`},
    {exports: `${user}/exports/share/applications/`, deploy: `${user}/app`}];
const inApp = `[Desktop Entry]
Type=Application
Name=Tide
# TV home opt-in
X-Luma-TV=true
X-Luma-TV-Tile=org.projectluma.Tide-tv
X-Luma-TV-Presentation=tv
X-Luma-TV[de]=false

[Desktop Action play]
X-Luma-TV-Tile=elsewhere
`;
const exported = '[Desktop Entry]\nType=Application\nName=Tide\nX-Flatpak=org.projectluma.Tide\n';
const files = `${system}/app/org.projectluma.Tide/current/active/files`;
write(`${files}/share/applications/org.projectluma.Tide.desktop`, inApp);
write(`${files}/share/luma-tv/org.projectluma.Tide-tv.svg`, '<svg xmlns="http://www.w3.org/2000/svg"/>');
write(`${system}/exports/share/applications/org.projectluma.Tide.desktop`, exported);
write(`${user}/app/org.projectluma.Weather/current/active/files/share/applications/org.projectluma.Weather.desktop`,
    '[Desktop Entry]\nName=Weather\nX-Luma-TV=true\nX-Luma-TV-Tile=org.projectluma.Weather-tv\n');
write(`${user}/app/org.example.Plain/current/active/files/share/applications/org.example.Plain.desktop`,
    '[Desktop Entry]\nName=Plain\n');

// The keys come from the deploy, and only from [Desktop Entry].
const tide = M.flatpakTv('org.projectluma.Tide.desktop', 'org.projectluma.Tide',
    `${roots[0].exports}org.projectluma.Tide.desktop`, roots, read);
assert(tide && tide.files === files, "a system Flatpak's deploy is found from its export");
assert(tide.keys['X-Luma-TV'] === 'true' && tide.keys['X-Luma-TV-Tile'] === 'org.projectluma.Tide-tv' &&
    tide.keys['X-Luma-TV-Presentation'] === 'tv', 'its TV keys are read');
assert(!('X-Luma-TV[de]' in tide.keys), 'localised and other groups are ignored');
const entry = M.tvEntry('org.projectluma.Tide.desktop', 'Tide', key => tide.keys[key] ?? null);
assert(entry && entry.tile === 'org.projectluma.Tide-tv' && entry.presentation === 'tv', 'it opts in like a native app');
assert(M.flatpakTile(tide.files, entry.tile) === `${files}/share/luma-tv/org.projectluma.Tide-tv.svg` &&
    read(M.flatpakTile(tide.files, entry.tile)) !== null, "the tile is the deploy's share/luma-tv/<tile>.svg");

const weather = M.flatpakTv('org.projectluma.Weather.desktop', 'org.projectluma.Weather',
    `${roots[1].exports}org.projectluma.Weather.desktop`, roots, read);
assert(weather?.keys['X-Luma-TV-Tile'] === 'org.projectluma.Weather-tv', "a user installation's Flatpak too");

// Fail closed: no keys, another installation, another folder, a hostile name.
assert(M.flatpakTv('org.example.Plain.desktop', 'org.example.Plain',
    `${roots[1].exports}org.example.Plain.desktop`, roots, read) === null, 'an app without TV keys stays off the TV home');
assert(M.flatpakTv('org.projectluma.Tide.desktop', 'org.projectluma.Tide',
    `${top}/opt/flatpak/exports/share/applications/org.projectluma.Tide.desktop`, roots, read) === null,
'an export outside the two installations is not followed');
assert(M.flatpakTv('org.projectluma.Tide.desktop', 'org.projectluma.Tide',
    `${roots[0].exports}sub/org.projectluma.Tide.desktop`, roots, read) === null, 'only the export folder itself');
assert(M.flatpakTv('org.projectluma.Tide.desktop', 'org.projectluma.Weather',
    `${roots[0].exports}org.projectluma.Tide.desktop`, roots, read) === null, "another app's deploy has no such file");
for (const bad of ['../../etc', 'org/projectluma/Tide', 'Tide', 'org.projectluma.Tide/..', '.hidden.app.id', ''])
    assert(M.flatpakTv('org.projectluma.Tide.desktop', bad, `${roots[0].exports}org.projectluma.Tide.desktop`, roots, read) === null,
        `a Flatpak ID ${JSON.stringify(bad)} is refused`);
for (const bad of ['../org.projectluma.Tide.desktop', 'a/b.desktop', 'org..projectluma.desktop', 'org.projectluma.Tide'])
    assert(M.flatpakTv(bad, 'org.projectluma.Tide', `${roots[0].exports}${bad}`, roots, read) === null,
        `a desktop ID ${JSON.stringify(bad)} is refused`);
assert(M.flatpakTv('org.projectluma.Tide.desktop', 'org.projectluma.Tide', `${roots[0].exports}org.projectluma.Tide.desktop`,
    roots, () => 'x'.repeat(70000)) === null, 'an oversized file is not parsed');
for (const bad of ['../x', 'a/b', '..', ''])
    assert(M.flatpakTile(files, bad) === null, `a tile name ${JSON.stringify(bad)} is refused`);
assert(M.flatpakTile(undefined, 'org.projectluma.Tide-tv') === null, 'a native app has no deploy tile');

// The TV home: export first, deploy when the export lost the keys; cached per app until the
// installed apps change; the deploy's tile before the icon theme.
assert(/key => info\.get_string\(key\)\) \?\? this\._flatpakEntry\(info\)\)/.test(home), 'the deploy only when the export has no TV keys');
assert(/'installed-changed', \(\) => \{\s*this\._flatpakTv\.clear\(\);\s*this\._loadApps\(\);/.test(home),
    'an install or update reads them again');
assert(/exports: '\/var\/lib\/flatpak\/exports\/share\/applications\/', deploy: '\/var\/lib\/flatpak\/app'/.test(home) &&
    /exports: `\$\{home\}\/\.local\/share\/flatpak\/exports\/share\/applications\/`/.test(home), 'the system and user installations only');
assert(!/Gio\.Subprocess|spawn_command_line|'flatpak'/.test(home), 'no flatpak subprocess');
assert(/const deployed = flatpakTile\(app\.files, app\.tile\);[\s\S]*?iconTheme\.lookup_icon\(app\.tile/.test(home),
    "the deploy's tile, then the icon theme");

if (gjs) GLib.spawn_command_line_sync(`rm -rf ${GLib.shell_quote(top)}`);
else fs.rmSync(top, {recursive: true, force: true});
print('tv-flatpak-policy: ok');
