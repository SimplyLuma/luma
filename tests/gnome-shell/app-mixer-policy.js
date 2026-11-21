// SPDX-License-Identifier: GPL-2.0-or-later
// The per-app mixer's model (0350, 0360): available only with
// org.projectluma.AudioDevices1 version 3, its meters run only while a view
// watches them, and the service's level meters never count as an app using
// the microphone. The Sound page itself is sound-page-policy.js.
// Run: gjs -m app-mixer-policy.js js/ui/status/volume.js js/ui/quickSettings.js \
//   js/ui/panel.js js/ui/lumaPresentation/fillSlider.js
// (node app-mixer-policy.js ... works too).
const gjs = typeof globalThis.ARGV !== 'undefined';
const args = gjs ? globalThis.ARGV : process.argv.slice(2);
const GLib = gjs ? (await import('gi://GLib')).default : null;
const fs = gjs ? null : await import('node:fs');
const read = path => (gjs ? new TextDecoder().decode(GLib.file_get_contents(path)[1])
    : fs.readFileSync(path, 'utf8'));
const print = gjs ? globalThis.print : console.log;

function assert(value, message) {
    if (!value)
        throw new Error(`FAIL: ${message}`);
}

const [volume, quickSettings, panel, fillSlider] = args.map(read);

const slice = (from, to) => {
    const start = volume.indexOf(from);
    const end = volume.indexOf(to, start);
    assert(start >= 0 && end > start, `volume.js has ${from}`);
    return volume.slice(start, end);
};
const model = slice('export const AppMixer =', 'let _appMixer');

// Version gate: without the service, or before version 3, the sheet is the
// output list.
assert(/const MIXER_VERSION = 3;/.test(volume), 'mixer needs interface version 3');
assert(/\(this\._proxy\.Version \?\? 0\) >= MIXER_VERSION/.test(model),
    'mixer is available only at version 3 or later');

// Views hold the model while they are on screen; nothing is fetched otherwise.
assert(/if \(!this\._holds\) \{\s*this\._appsDirty = true;\s*return;/.test(model),
    'a change nobody is looking at is only remembered as dirty');
assert(/const targets = this\._meterHolds > 0 && this\.available/.test(model),
    'the service watch follows the meter holders');
assert(/if \(serial !== this\._watchSerial\) \{\s*this\._proxy\.UnwatchLevelsAsync\(watch\)/.test(model),
    'a watch that returns after the rows closed is dropped at once');
assert(/if \(watch !== this\._watch \|\| !watch\)/.test(model), 'only our own Levels are shown');

// Transport, raising and switching the output go through the model.
assert(/playPause\(id\) \{\s*this\._proxy\.ApplicationPlayPauseAsync\(id\)/.test(model), 'play/pause is the service\'s');
assert(/raise\(id\) \{[\s\S]*?app\.shellApp\.activate\(\);[\s\S]*?this\._proxy\.RaiseApplicationAsync\(id\)/.test(model),
    'raising uses the Shell\'s own window, else the service');
assert(/setDefaultOutput\(name\) \{[\s\S]*?control\.set_default_sink\(stream\);/.test(model),
    'a chip switches the default output');

// The service's level probes are not an app recording.
assert(/const LEVEL_METER_APPLICATION_IDS = \[[\s\S]*?AUDIO_DEVICES_BUS_NAME,\s*\];/.test(volume),
    'the service id is a level meter');
assert(/get_source_outputs\(\)\.some\(isRecordingApplication\)/.test(volume),
    'the microphone indicator skips level meters');
assert(/input\.getRecordingOutputs\?\.\(\)/.test(quickSettings),
    'the microphone strip names recording apps only');
assert(/_input\?\.getRecordingOutputs\?\.\(\)/.test(panel),
    'the status well names recording apps only');

// System sounds and the shared level.
assert(/const SYSTEM_SOUNDS_ICON = 'lumaui-bell-symbolic';/.test(volume), 'System sounds uses the bell');
assert(/'meter': GObject\.ParamSpec\.double/.test(fillSlider), 'FillSlider draws a meter');
assert(/has_style_class_name\('lumaui-muted-level'\)/.test(fillSlider), 'FillSlider draws muted quietly');
// A page's settings link is pinned under the scrolled list.
assert(/_pinPageLink\(menu\) \{/.test(quickSettings) && /this\._pinPageLink\(menu\);/.test(quickSettings) &&
    /this\._pinPageLink\(null\);/.test(quickSettings), 'a page pins its settings link while open');
assert(/\(menu\._lumaPlainPage \? tallest : Math\.min\(420, tallest\)\) - foot/.test(quickSettings),
    'a list page stops at 420 px; a plain page may use the work area');

print('app-mixer-policy: PASS');
