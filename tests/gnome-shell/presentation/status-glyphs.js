// SPDX-License-Identifier: Apache-2.0
import {statusGlyph} from '../../../src/luma-shell-presentation/layout.js';
function assert(value, message) { if (!value) throw Error(message); }
const cases = {
    'network-wireless-signal-excellent-symbolic': 'lumaui-wifi-symbolic',
    'network-wireless-signal-good-symbolic': 'lumaui-wifi-high-symbolic',
    'network-wireless-signal-weak-symbolic': 'lumaui-wifi-zero-symbolic',
    'network-wireless-disabled-symbolic': 'luma-wifi-off-symbolic',
    'network-cellular-signal-ok-symbolic': 'lumaui-signal-medium-symbolic',
    'network-cellular-offline-symbolic': 'lumaui-signal-zero-symbolic',
    'audio-volume-muted-symbolic': 'lumaui-volume-x-symbolic',
    'audio-volume-medium-symbolic': 'lumaui-volume-1-symbolic',
    'battery-level-90-symbolic': 'lumaui-battery-full-symbolic',
    'battery-level-50-symbolic': 'lumaui-battery-medium-symbolic',
    'battery-level-10-symbolic': 'lumaui-battery-warning-symbolic',
    'battery-level-40-charging-symbolic': 'lumaui-battery-charging-symbolic',
    'battery-level-100-charged-symbolic': 'lumaui-battery-full-symbolic',
    'org.gnome.Settings-accessibility-symbolic': 'lumaui-accessibility-symbolic',
    'accessibility-menu-symbolic': 'lumaui-accessibility-symbolic',
    'airplane-mode-symbolic': 'lumaui-plane-symbolic',
};
for (const [name, glyph] of Object.entries(cases))
    assert(statusGlyph(name) === glyph, `${name} must draw as ${glyph}, got ${statusGlyph(name)}`);
assert(statusGlyph('lumaui-wifi-symbolic') === null, 'a LumaUI glyph is left as it is');
assert(statusGlyph('some-unknown-symbolic') === null, 'an icon without a glyph is left as it is');
print('status glyphs ok');
