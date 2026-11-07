// SPDX-License-Identifier: GPL-2.0-or-later
// The date menu is the calendar column alone: calendar, today and events.
// Notifications live in the notification tray, so the menu builds no message
// list and its area holds only the calendar column.
// Run: gjs -m date-menu-calendar-only-policy.js js/ui/dateMenu.js
// (node date-menu-calendar-only-policy.js ... works too).
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

const menu = read(args[0]);
const start = menu.indexOf('class DateMenuButton');
assert(start > 0, 'dateMenu.js has the DateMenuButton');
const button = menu.slice(start);

assert(!/CalendarMessageList|_messageList/.test(button),
    'the date menu builds no notification list');
const children = button.match(/hbox\.add_child\((\w+)\)/g) ?? [];
assert(children.length === 1 && children[0] === 'hbox.add_child(vbox)',
    `the calendar area holds only the calendar column: ${children.join(', ')}`);
assert(/new EventsSection\(\)/.test(button), 'the events section stays');

print('date menu calendar only: ok');
