// SPDX-License-Identifier: GPL-2.0-or-later
// Runs against the actual prepared/packaged panel.js with real Pango shaping.
// Run: gjs -m clock-line-metrics.js panel.js
import Gio from 'gi://Gio';
import Pango from 'gi://Pango';
import PangoCairo from 'gi://PangoCairo';

function check(value, message) {
    if (!value)
        throw new Error(message);
}
function method(source, name) {
    const marker = `    ${name}(`;
    const start = source.indexOf(marker);
    const body = source.indexOf('{', start);
    const end = source.indexOf('\n    }', body);
    check(start >= 0 && body >= 0 && end >= 0, `Missing ${name}`);
    return source.slice(body + 1, end);
}
const [loaded, bytes] = Gio.File.new_for_path(ARGV[0]).load_contents(null);
check(loaded && bytes.length > 0, 'Actual panel source must be present');
const update = new Function('label', 'Pango', 'St', 'global', 'STATUS_CLOCK_LEADING',
    method(new TextDecoder().decode(bytes), '_setStatusLineHeight'));
const context = PangoCairo.FontMap.get_default().create_context();
let cases = 0;
for (const family of ['Figtree', 'Sans']) {
 for (const scale of [1, 2]) {
  for (const size of [21, 11.5, 26, 32]) {
    for (const text of ['9:02', '00:28:59', 'p.m.', '午後', 'Tue, Oct 6', 'الأربعاء']) {
        const layout = Pango.Layout.new(context);
        const font = Pango.FontDescription.from_string(`${family} Semi-Bold`);
        font.set_absolute_size(size * scale * Pango.SCALE);
        const resolvedFamily = context.get_font_map().load_font(context, font).describe().get_family();
        check(family !== 'Figtree' || resolvedFamily.includes('Figtree'),
            `Figtree fixture must shape the installed font, got ${resolvedFamily}`);
        layout.set_font_description(font);
        layout.set_text(text, -1);
        const [, natural] = layout.get_pixel_extents();
        const attrs = new Pango.AttrList();
        attrs.change(Pango.attr_foreground_new(1000, 2000, 3000));
        attrs.change(Pango.attr_font_features_new('tnum'));
        attrs.change(Pango.attr_line_height_new_absolute((size + 3) * scale * Pango.SCALE));
        let actual = attrs;
        const label = {clutter_text: {
            get_attributes: () => actual,
            set_attributes(value) { actual = value; layout.set_attributes(value); },
        }};
        const owner = {_statusFontSize: () => size};
        const St = {ThemeContext: {get_for_stage: () => ({scale_factor: scale})}};
        update.call(owner, label, Pango, St, {stage: null}, 3);
        const [, measured] = layout.get_pixel_extents();
        check(measured.height === natural.height,
            `${family} ${text} at ${size}px, scale ${scale}: expected natural ${natural.height}px line, got ${measured.height}px`);
        check(actual.get_iterator().get(Pango.AttrType.ABSOLUTE_LINE_HEIGHT) === null,
            `${text} at ${size}px: obsolete absolute line height survives`);
        check(actual.get_iterator().get(Pango.AttrType.FOREGROUND) !== null,
            'Native clock ink attribute must survive');
        check(actual.get_iterator().get(Pango.AttrType.FONT_FEATURES) !== null,
            'Native tabular-number attribute must survive');
        cases++;
    }
}
 }
}
check(cases === 96, `Expected 96 shaped-font cases, ran ${cases}`);
print(`Clock natural line metrics: PASS (${cases} real Pango cases)`);
