// SPDX-License-Identifier: Apache-2.0
// Execute the packaged transition without starting an authentication session.
const [ok, bytes] = imports.gi.GLib.file_get_contents(ARGV[0]);
if (!ok)
    throw new Error('Cannot read packaged unlock source');
const source = new TextDecoder().decode(bytes);
const body = source.match(/_setTransitionProgress\(progress\) \{([\s\S]*?)\n    \}/)?.[1];
const scale = source.match(/const FADE_OUT_SCALE = ([.\d]+);/)?.[1];
if (!body || !scale)
    throw new Error('Missing unlock transition or scale declaration');
const transition = new Function('progress', 'FADE_OUT_SCALE', body);
for (const progress of [0, 0.5, 1]) {
    const actor = () => ({set(values) { Object.assign(this, values); }});
    const dialog = {_promptBox: actor(), _clock: actor(), _otherUserButton: actor()};
    transition.call(dialog, progress, Number(scale));
    if (dialog._promptBox.visible !== (progress > 0) ||
        dialog._promptBox.opacity !== 255 * progress ||
        dialog._otherUserButton.reactive !== (progress > 0) ||
        !Number.isFinite(dialog._otherUserButton.scale_x))
        throw new Error(`Invalid unlock transition at ${progress}`);
}
print('Packaged unlock transition: PASS');
