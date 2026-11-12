// SPDX-License-Identifier: Apache-2.0
// Logical geometry adapts presentation; it never chooses the presentation mode.
export function formOf(width, height) {
    const short = Math.min(width, height);
    const long = Math.max(width, height);
    if (long < 600)
        return 'pocket';
    if (short >= 760 && long >= 1024)
        return 'tablet';
    return short >= 560 ? 'fold' : 'phone';
}

// User density is a preference, not permission to create undersized targets.
// Keep automatic geometry unchanged when no override is set.
function preferredCount(requested, automatic, available, minimumCell, minimum) {
    if (!Number.isInteger(requested) || requested <= 0) return automatic;
    const limit = Math.max(1, Math.min(12, Math.floor(available / minimumCell)));
    return Math.min(limit, Math.max(Math.min(minimum, limit), requested));
}
function preferredIcon(choice, automatic, width, height, labels) {
    const wanted = {small: 32, medium: 48, large: 64}[choice] ?? automatic;
    return Math.floor(Math.max(20, Math.min(wanted, width - 12, height - (labels ? 24 : 12))));
}

// Physical targets for handheld Home, in millimetres at the user's zoom: a
// comfortable app cell, the narrowest cell a phone's four columns may use,
// the densest automatic cell, a row (icon, label and air), an icon and a
// label. A page needs MIN_ROWS comfortable rows; a short screen (a square
// keyboard phone) trades column width for rows instead.
const CELL_MM = 17, PHONE_CELL_MM = 13, DENSE_CELL_MM = 12.5, ROW_MM = 17, ICON_MM = 10, WIDE_ICON_MM = 11.4, LABEL_MM = 2;
const MIN_ROWS = 4;
// The wide layout's least cell height; its rows add an 8 px gap.
const WIDE_CELL = 100;
// The px the generated widget sheet (generate-home-widget-styles.py) writes
// its token sizes against: each is an em of 14 px.
const WIDGET_TYPE_BASE = 14;

// Millimetres per logical pixel of the primary monitor, and the user's zoom:
// its scale over the scale Mutter picks by default for the panel. Mutter
// reports the last applied scale as a mode's preferred one, so the default is
// derived as its calculate_scale() does (meta-monitor.c): the supported scale
// nearest the panel's DPI over 135 (110 from 20 inches). Inputs are DisplayConfig
// GetCurrentState and GetResources replies, deep-unpacked. Null when the
// panel reports no plausible size (no EDID/DT size, or a 96 dpi placeholder).
export function displayDensity(state, resources, x, y) {
    const value = v => v?.deep_unpack ? v.deep_unpack() : v;
    const logical = state?.[2]?.find(entry => entry[0] === x && entry[1] === y);
    const connector = logical?.[5]?.[0]?.[0];
    const mode = state?.[1]?.find(entry => entry[0][0] === connector)?.[1]
        ?.find(entry => value(entry[6]?.['is-current']));
    const output = resources?.[2]?.find(entry => entry[4] === connector);
    if (!logical || !mode || !output) return null;
    const widthMm = value(output[7]?.['width-mm']), heightMm = value(output[7]?.['height-mm']);
    const [, nativeWidth, nativeHeight, , , supported = []] = mode;
    const scale = logical[2];
    if (!(widthMm > 0 && heightMm > 0 && nativeWidth > 0 && nativeHeight > 0 && scale > 0)) return null;
    // Some EDIDs store only an aspect ratio; the panel's shape must match its pixels.
    const pixelAspect = Math.max(nativeWidth, nativeHeight) / Math.min(nativeWidth, nativeHeight);
    const mmAspect = Math.max(widthMm, heightMm) / Math.min(widthMm, heightMm);
    if (Math.abs(mmAspect / pixelAspect - 1) > 0.15) return null;
    const diagonalMm = Math.hypot(widthMm, heightMm);
    const mmPerNative = diagonalMm / Math.hypot(nativeWidth, nativeHeight);
    const perfect = 25.4 / mmPerNative / (diagonalMm < 20 * 25.4 ? 135 : 110);
    const automatic = supported.length
        ? supported.reduce((best, s) => Math.abs(s - perfect) < Math.abs(best - perfect) ? s : best) : scale;
    const zoom = scale / automatic;
    // A UI pixel at the default scale is 0.1-0.4 mm on any real screen, and
    // nobody zooms past half or twice it: a 96 dpi placeholder size fails one.
    const automaticPixel = mmPerNative * automatic;
    if (automaticPixel < 0.1 || automaticPixel > 0.4 || zoom < 0.5 || zoom > 2) return null;
    return {mmPerPx: mmPerNative * scale, zoom};
}

export function homeLayout(width, height, mode, count, metrics = {}, preferences = {}, density = null) {
    // Widgets and apps share the same whole icon-cell grid.
    // Density depends on usable geometry, independently of device or mode.
    // Logical px for a physical size at the user's zoom, and back.
    const px = mm => mm * density.zoom / density.mmPerPx;
    const mmOf = pixels => pixels * density.mmPerPx / density.zoom;
    if (width <= 480 && height >= 280) {
        const inset = 8;
        const top = 44;
        let dockHeight = 56;
        const dockBottom = 6;
        const dotsHeight = 16;
        const contentWidth = width - 2 * inset;
        const reserved = () => dockHeight + dockBottom + dotsHeight + 4;
        let automaticColumns, roomy = false;
        if (density) {
            // Four columns on any phone whose cells stay PHONE_CELL_MM wide,
            // more on wider screens; on a short screen, the densest cell that
            // still fits, so a page keeps enough apps.
            const contentMm = mmOf(contentWidth);
            const comfortable = Math.min(6, Math.max(contentMm >= 4 * PHONE_CELL_MM ? 4 : 3,
                Math.round(contentMm / CELL_MM)));
            roomy = mmOf(height - top - reserved()) / ROW_MM >= MIN_ROWS;
            automaticColumns = roomy ? comfortable
                : Math.max(comfortable, Math.min(6, Math.floor(contentMm / DENSE_CELL_MM)));
            // The dock's buttons follow Home's icons (icon + 4 + two 6 px gaps).
            if (roomy) dockHeight = Math.max(56, Math.round(Math.min(64, px(ICON_MM))) + 16);
        } else {
            // Fit five columns when 68px cells fit, and reduce density on narrower
            // displays. Additional columns must not also compress the row spacing.
            automaticColumns = Math.max(3, Math.min(5, Math.floor(contentWidth / 68)));
        }
        const bottom = reserved();
        const availableHeight = Math.max(1, height - top - bottom);
        const iconColumns = preferredCount(preferences.columns, automaticColumns, contentWidth, 56, 3);
        const cellWidth = contentWidth / iconColumns;
        const minimumRow = Math.max(56, metrics.cellHeight ?? 0,
            Math.round(contentWidth / Math.min(iconColumns, 4) * 0.78));
        const automaticRows = roomy
            ? Math.max(2, Math.min(32, Math.round(mmOf(availableHeight) / ROW_MM)))
            : Math.max(2, Math.min(32, Math.floor(availableHeight / minimumRow)));
        const rows = preferredCount(preferences.rows, automaticRows, availableHeight, 56, 2);
        const rowHeight = availableHeight / rows;
        const automaticIcon = Math.min(64, rowHeight - 24, cellWidth * 0.66, density ? px(ICON_MM) : Infinity);
        const iconSize = preferredIcon(preferences.iconSize, automaticIcon,
            cellWidth, rowHeight, preferences.showLabels !== false);
        const labelSize = roomy ? Math.max(10, Math.min(13, Math.round(px(LABEL_MM)))) : 10;
        // Widget text on a roomy phone: the token sizes of a wide-layout
        // widget, scaled by Home's row over the wide layout's least row and
        // never above the tokens. A dense grid (no density, or a short
        // screen) keeps the compact sheet's fixed sizes (widgetFontSize null).
        const widgetFontSize = roomy
            ? Math.round(WIDGET_TYPE_BASE * Math.min(1, rowHeight / (WIDE_CELL + 8)) * 100) / 100 : null;
        const columns = iconColumns;
        const capacity = rows * iconColumns;
        return {width, height, form: formOf(width, height), compact: true, roomy, widgetFontSize, inset,
            contentWidth, columns, iconColumns, appColumns: 1,
            widgetColumns: Math.floor(iconColumns / 2), top, bottom, dockHeight, dockBottom,
            dotsHeight, iconSize, labelSize, cellHeight: rowHeight - 4, rowHeight,
            firstRows: rows, otherRows: rows, firstCapacity: capacity,
            laterCapacity: capacity, pages: Math.max(1, Math.ceil(count / capacity))};
    }
    const tablet = mode === 'tablet';
    const inset = tablet ? Math.min(56, width / 16) : 20;
    const contentWidth = Math.max(1, width - 2 * inset);
    // Wider screens keep their cell proportions around a physical icon.
    const automaticIcon = density ? Math.max(48, Math.min(72, Math.round(px(WIDE_ICON_MM)))) : 60;
    const cellWidth = Math.max(Math.round((tablet ? 112 : 84) * automaticIcon / 60), metrics.minimumCellWidth ?? 0);
    const columns = preferredCount(preferences.columns, Math.max(1, Math.floor(contentWidth / cellWidth)),
        contentWidth, 56, 3);
    const top = height < 440 ? 44 : 64;
    const bottom = 156;
    const cellHeight = Math.max(WIDE_CELL, metrics.cellHeight ?? 0);
    let rowHeight = cellHeight + 8;
    if (preferences.rows > 0) {
        const rows = preferredCount(preferences.rows, 1, height - top - bottom, 56, 2);
        rowHeight = Math.max(1, (height - top - bottom) / rows);
    }
    const firstRows = Math.max(0, Math.floor((height - top - bottom + 8) / rowHeight));
    const otherRows = Math.max(1, Math.floor((height - top - bottom + 8) / rowHeight));
    const firstCapacity = firstRows * columns;
    const laterCapacity = otherRows * columns;
    const pages = Math.max(1, 1 + Math.ceil(Math.max(0, count - firstCapacity) / laterCapacity));
    const iconSize = preferredIcon(preferences.iconSize, automaticIcon, contentWidth / columns, rowHeight, preferences.showLabels !== false);
    return {width, height, form: formOf(width, height), inset, contentWidth, columns, top, bottom, cellHeight, rowHeight, iconSize, firstRows, otherRows,
        firstCapacity, laterCapacity, pages};
}

// Ink for the status bar over sampled colours (0-255 channels): dark ink
// (#191b1f) when it out-contrasts white over the median sample.
export function inkFor(colors) {
    const linear = c => (c /= 255) <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    const luminance = colors.map(({red, green, blue}) =>
        0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)).sort((a, b) => a - b);
    if (!luminance.length) return null;
    const middle = luminance.length / 2;
    const median = luminance.length % 2 ? luminance[Math.floor(middle)]
        : (luminance[middle - 1] + luminance[middle]) / 2;
    // Equal contrast against white and #191b1f is at luminance 0.203.
    return median > 0.203 ? 'dark' : 'light';
}

// Desktop entries Home leaves out although they show elsewhere: the Shell's
// own Extensions app, Settings' per-panel entries (org.gnome.Settings.<panel>,
// not Settings itself) and search providers.
export function hiddenFromHome(id, name) {
    return /^(org\.gnome\.Shell\.Extensions\.|org\.gnome\.Settings\.(?!desktop$)|luma-search|search\.)/i.test(id) ||
        /^search$/i.test(name.trim());
}

export function pageRange(layout, page, count) {
    const start = page === 0 ? 0 : layout.firstCapacity + (page - 1) * layout.laterCapacity;
    const capacity = page === 0 ? layout.firstCapacity : layout.laterCapacity;
    return [start, Math.min(count, start + capacity)];
}

// A panel follows displacement in both directions; release alone decides the
// endpoint. Velocity is pixels/second, ignored after a stationary hold.
export function panelProgress(start, delta, travel) {
    return Math.max(0, Math.min(1, start + delta / Math.max(1, travel)));
}
export function panelSnap(progress, velocity = 0) {
    if (Math.abs(velocity) >= 600) return velocity > 0 ? 1 : 0;
    return progress >= .35 ? 1 : 0;
}

// Lock-screen visibility is Mobile-only, and never grants app activation.
export function notificationAccess({mode, locked, greeter, hasNotifications,
    showOnLock = false, sourceEnabled = true, sourceOnLock = true, detailsOnLock = true}) {
    const visible = !greeter && sourceEnabled && (locked
        ? mode === 'mobile' && showOnLock && sourceOnLock : hasNotifications);
    return {visible: Boolean(visible), details: Boolean(visible && (!locked || detailsOnLock)),
        activate: Boolean(visible && !locked)};
}

// Read the painted position when a finger interrupts an in-flight settle.
export function pageProgress(translation, width, pages, rtl = false) {
    if (width <= 0 || pages < 2) return 0;
    const progress = rtl ? translation / width + pages - 1 : -translation / width;
    return Math.max(0, Math.min(pages - 1, progress));
}

export function visiblePageRange(progress, pages) {
    const bounded = Math.max(0, Math.min(pages - 1, progress));
    return [Math.floor(bounded), Math.ceil(bounded)];
}

// Pull regions use painted chrome geometry. Padding grows the notification
// target without moving the nib or overlapping the clock/right indicators.
export function statusPullZone(x, y, {status, clock = null, island = null, right, scale = 1}) {
    if (x < status.x || x > status.x + status.width || y < status.y ||
        y > status.y + Math.max(status.height, 44 * scale)) return null;
    if (island) {
        const center = island.x + island.width / 2;
        const half = Math.max(96 * scale, island.width + 48 * scale) / 2;
        const left = Math.max(status.x, center - half, clock ? clock.x + clock.width + 8 * scale : status.x);
        const end = Math.min(status.x + status.width, center + half, right.x - 8 * scale);
        if (x >= left && x <= end) return 'notifications';
    }
    if (clock && x >= clock.x && x <= clock.x + clock.width) return 'options';
    return x >= Math.min(right.x, status.x + status.width / 2) ? 'options' : null;
}

// The LumaUI (Lucide) glyph that draws a GNOME status icon's state, or null
// to leave an icon the family has no glyph for. GNOME's indicators keep their
// own state logic; the handheld status bar only redraws their states.
const STATUS_GLYPHS = [
    [/^network-wireless-(signal-excellent|connected)/, 'lumaui-wifi-symbolic'],
    [/^network-wireless-signal-good/, 'lumaui-wifi-high-symbolic'],
    [/^network-wireless-(signal-ok|acquiring)/, 'lumaui-wifi-low-symbolic'],
    [/^network-wireless-signal-(weak|none)/, 'luma-status-wifi-zero-symbolic'],
    [/^network-wireless-(disabled|offline|no-route)/, 'luma-wifi-off-symbolic'],
    [/^network-wireless-hotspot/, 'lumaui-wifi-symbolic'],
    [/^network-cellular-signal-excellent/, 'luma-status-signal-symbolic'],
    [/^network-cellular-signal-good/, 'luma-status-signal-high-symbolic'],
    [/^network-cellular-signal-ok/, 'luma-status-signal-medium-symbolic'],
    [/^network-cellular-signal-weak/, 'luma-status-signal-low-symbolic'],
    [/^network-cellular-/, 'luma-status-signal-zero-symbolic'],
    [/^network-wired/, 'lumaui-ethernet-port-symbolic'],
    [/^network-vpn/, 'lumaui-shield-symbolic'],
    [/^airplane-mode/, 'lumaui-plane-symbolic'],
    [/^audio-volume-muted/, 'lumaui-volume-x-symbolic'],
    [/^audio-volume-low/, 'luma-status-volume-symbolic'],
    [/^audio-volume-medium/, 'lumaui-volume-1-symbolic'],
    [/^audio-volume-(high|overamplified)/, 'lumaui-volume-2-symbolic'],
    [/^microphone-sensitivity-muted/, 'lumaui-mic-off-symbolic'],
    [/^(microphone-sensitivity|audio-input-microphone)/, 'lumaui-mic-symbolic'],
    [/^bluetooth-/, 'lumaui-bluetooth-symbolic'],
    [/^location-services-/, 'lumaui-map-pin-symbolic'],
    [/^camera-web/, 'lumaui-camera-active-symbolic'],
    [/^notifications-disabled/, 'luma-moon-symbolic'],
    [/^night-light/, 'lumaui-sun-moon-symbolic'],
    [/^power-profile-/, 'luma-gauge-symbolic'],
    [/^rotation-locked/, 'lumaui-rotate-cw-symbolic'],
    [/^media-record/, 'luma-capture-record-screen-symbolic'],
    [/^accessibility-|accessibility-symbolic$/, 'lumaui-accessibility-symbolic'],
];

export function statusGlyph(name) {
    if (!name || name.startsWith('luma'))
        return null;
    const battery = /^battery-level-(\d+)(-charging|-charged)?/.exec(name);
    if (battery) {
        if (battery[2] === '-charging')
            return 'luma-status-battery-charging-symbolic';
        const level = Number(battery[1]);
        if (battery[2] === '-charged' || level >= 80)
            return 'luma-status-battery-full-symbolic';
        return level >= 40 ? 'lumaui-battery-medium-symbolic'
            : level >= 20 ? 'luma-status-battery-low-symbolic' : 'luma-status-battery-warning-symbolic';
    }
    if (/^battery-(missing|empty|caution)/.test(name))
        return 'luma-status-battery-warning-symbolic';
    return STATUS_GLYPHS.find(([pattern]) => pattern.test(name))?.[1] ?? null;
}
