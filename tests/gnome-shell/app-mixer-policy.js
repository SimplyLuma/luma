// SPDX-License-Identifier: GPL-2.0-or-later
// The per-app mixer in the Sound output page (0350): it appears only with
// org.projectluma.AudioDevices1 version 2, its meters run only while its rows
// are on screen, and the service's level meters never count as an app using
// the microphone.
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
const section = slice('const AppMixerSection', '// Each Gvc.MixerControl');

// Version gate: without the service, or before version 2, nothing changes.
assert(/const MIXER_VERSION = 2;/.test(volume), 'mixer needs interface version 2');
assert(/\(this\._proxy\.Version \?\? 0\) >= MIXER_VERSION/.test(model),
    'mixer is available only at version 2 or later');
assert(/mixerSection\.actor\.visible = this\.appMixer\.available && mixer\.visible;/.test(volume),
    'the page shows the mixer only while it is available and has rows');

// Meters: watched while the rows are mapped, unwatched when they are not.
assert(/this\._presence\.connectObject\('notify::mapped', \(\) => this\._syncShown\(\), this\)/.test(section),
    'mixer follows whether its page is on screen');
assert(/new AppMixerSection\(this\.appMixer, this\.menu\.actor\)/.test(volume),
    'the page is the Sound output sheet');
assert(/if \(shown\) \{\s*this\._mixer\.hold\(\);\s*this\._mixer\.watchLevels\(\);/.test(section),
    'on screen: held and metered');
assert(/\} else \{\s*this\._mixer\.unwatchLevels\(\);\s*this\._mixer\.release\(\);/.test(section),
    'off screen: meters stop and the model is released');
assert(/'destroy', \(\) => \{\s*this\._setShown\(false\);/.test(section),
    'destroying the rows stops their meters');
assert(/const targets = this\._meterHolds > 0 && this\.available/.test(model),
    'the service watch follows the meter holders');
assert(/if \(serial !== this\._watchSerial\) \{\s*this\._proxy\.UnwatchLevelsAsync\(watch\)/.test(model),
    'a watch that returns after the rows closed is dropped at once');
assert(/if \(watch !== this\._watch \|\| !watch\)/.test(model), 'only our own Levels are shown');

// The service's level probes are not an app recording.
assert(/const LEVEL_METER_APPLICATION_IDS = \[[\s\S]*?AUDIO_DEVICES_BUS_NAME,\s*\];/.test(volume),
    'the service id is a level meter');
assert(/get_source_outputs\(\)\.some\(isRecordingApplication\)/.test(volume),
    'the microphone indicator skips level meters');
assert(/input\.getRecordingOutputs\?\.\(\)/.test(quickSettings),
    'the microphone strip names recording apps only');
assert(/_input\?\.getRecordingOutputs\?\.\(\)/.test(panel),
    'the status well names recording apps only');

// System sounds row and the shared level.
assert(/const SYSTEM_SOUNDS_ICON = 'lumaui-bell-symbolic';/.test(volume), 'System sounds uses the bell');
assert(/new FillSlider\(0\)/.test(volume), 'app levels are the shared filled slider');
assert(/'meter': GObject\.ParamSpec\.double/.test(fillSlider), 'FillSlider draws a meter');

print('app-mixer-policy: PASS');
