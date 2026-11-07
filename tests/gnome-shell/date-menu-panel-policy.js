// SPDX-License-Identifier: GPL-2.0-or-later
// The date menu's calendar panel: the events list shows what is coming up
// (at most four rows, then "N more"), or the chosen day; choosing that day
// again goes back to today; rows open the event in Luma Calendar. The month on
// show pages without choosing a day, and the calendar server sends each
// event's place and colour.
// Run: gjs -m date-menu-panel-policy.js js/ui/dateMenu.js js/ui/calendar.js \
//   src/calendar-server/gnome-shell-calendar-server.c
// (node date-menu-panel-policy.js ... works too).
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

const [menu, calendar, server] = args.map(read);

const start = menu.indexOf('const EventsSection');
const end = menu.indexOf('const WorldClocksSection');
assert(start > 0 && end > start, 'dateMenu.js has the EventsSection');
const events = menu.slice(start, end);

assert(/const MAX_EVENT_ROWS = 4;/.test(menu), 'at most four event rows');
assert(/_isToday\(this\._date\)\s*\)?\s*\n?\s*this\._showUpcoming/.test(events),
    'today lists what is coming up');
assert(/Calendar\.UPCOMING_DAYS/.test(events), 'upcoming runs into next month');
for (const text of ['Nothing coming up.', 'Nothing on %s.', '%d more this week',
    '%d more coming up', '%d more on %s', 'Open Calendar'])
    assert(events.includes(`'${text}'`), `the list says "${text}"`);
assert(/this\._openCalendar\(_calendarArgs\(event\.id, day\)\)/.test(events),
    'a row opens its event in Calendar');
assert(/style_class: 'event-box',\s*can_focus: true/.test(events),
    'each row is a focusable button');
assert(/accessible_name: \[event\.summary, time, event\.location\]/.test(events),
    'a row says its title, time and place');
assert(/Gio\.DesktopAppInfo\.new\(CALENDAR_APP_ID\)/.test(events) &&
    /const CALENDAR_APP_ID = 'org\.projectluma\.Calendar\.desktop';/.test(menu),
    'Calendar is Luma Calendar');

// Calendar's command line: --event-uid=<uid> --event-day=<day>, or the day alone
const argsStart = menu.indexOf('function _calendarArgs');
const argsSource = menu.slice(argsStart, menu.indexOf('\n}\n', argsStart) + 2);
const formatDateWithCFormatString = (d, f) => {
    assert(f === '%Y-%m-%d', 'days are passed as YYYY-MM-DD');
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
const calendarArgs = new Function('formatDateWithCFormatString',
    `${argsSource}; return _calendarArgs;`)(formatDateWithCFormatString);
const day = new Date(2026, 10, 12);
assert(JSON.stringify(calendarArgs('local\nabc\n', day)) ===
    '["--event-uid=abc","--event-day=2026-11-12"]', 'an event opens by its uid on its day');
assert(JSON.stringify(calendarArgs('local\nabc\n20261112T090000Z', day)) ===
    '["--event-uid=abc","--event-day=2026-11-12"]', 'a repeating event opens on its day');
assert(JSON.stringify(calendarArgs(null, day)) === '["--event-day=2026-11-12"]', 'a day alone');

const grid = calendar.slice(calendar.indexOf('export const Calendar'));
assert(/export const UPCOMING_DAYS = \d+;/.test(calendar), 'calendar.js exports UPCOMING_DAYS');
assert(/sameDay\(button\._date, this\._selectedDate\)\s*\?\s*new Date\(\)/.test(grid),
    'choosing the chosen day again goes back to today');
assert(/_onPrevMonthButtonClicked\(\) \{[^}]*this\._showMonth\(-1\)/.test(grid) &&
    /_onNextMonthButtonClicked\(\) \{[^}]*this\._showMonth\(1\)/.test(grid),
    'the arrows page months without choosing a day');
assert(/'key-press-event', this\._onDayKeyPress/.test(grid), 'arrow keys walk the days');
assert(/ngettext\('%d event', '%d events'/.test(grid) && /'nothing planned'/.test(grid),
    'each day says how many events it has');
assert(/this\._todayButton\.visible = !sameMonth\(this\._shownMonth, now\)/.test(grid),
    'Today shows when another month is on show');
assert(/_requestRange\(\) \{/.test(grid) && !/this\._eventSource\.requestRange\(beginDate, iter\)/.test(grid),
    'the range asked for covers the days on show and the list');
assert(/extras\.location\?\.unpack\(\)/.test(calendar) && /extras\.color\?\.unpack\(\)/.test(calendar),
    'events carry their place and colour');

assert(/"location",\s*g_variant_new_string \(appt->location\)/.test(server) &&
    /"color",\s*g_variant_new_string \(appt->color\)/.test(server),
    'the calendar server sends place and colour');

print('date menu calendar panel: ok');
