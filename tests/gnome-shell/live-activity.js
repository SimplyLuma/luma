// SPDX-License-Identifier: GPL-2.0-or-later
// Classifier checks for the live island (js/ui/liveActivity.js).
// Run: gjs -m live-activity.js path/to/liveActivity.js
import GLib from 'gi://GLib';
import System from 'system';
const L = await import(
    GLib.filename_to_uri(GLib.canonicalize_filename(ARGV[0], GLib.get_current_dir()), null));

let failures = 0;
const check = (name, actual, expected) => {
    const a = JSON.stringify(actual), e = JSON.stringify(expected);
    if (a !== e) {
        failures++;
        printerr(`FAIL ${name}: expected ${e}, got ${a}`);
    }
};

const item = (extension, application_id = 'org.example.App') => ({
    application_id, publication_id: `p-${extension.id}`, extension: {
        schema_version: '0.1', privacy: 'private', progress: -1, actions: [],
        expires_at: '2026-09-16T16:41:00+00:00', ...extension,
    },
});

// Exactly what luma-calls published for the owner's Claude Desktop voice call:
// a category, a start, and a subtitle carrying the elapsed clock.
const claudeCall = item({
    id: 'calls.active', app_id: 'org.projectluma.Calls', category: 'call',
    title: 'Claude', subtitle: 'Claude · 03:23',
    starts_at: '2026-09-16T16:40:12.402119+00:00',
    actions: [
        {id: 'call.mute', label: 'Mute microphone', risk: 'low', enabled: true},
        {id: 'call.camera', label: 'Turn camera off', risk: 'low', enabled: false,
            description: 'Claude is not using the camera.'},
        {id: 'call.hangup', label: 'End call', risk: 'low', enabled: false,
            description: 'Claude does not allow another application to end its calls.'},
    ],
}, 'org.projectluma.Calls');
const calendar = item({
    id: 'agenda.next', category: 'event', title: 'Design review',
    subtitle: 'Up next · 20 min', starts_at: '2026-09-16T17:00:00+00:00',
    actions: [{id: 'agenda.open', label: 'Open', risk: 'passive', enabled: true}],
}, 'org.projectluma.Calendar');
const media = item({id: 'm', category: 'media', title: 'Manchild', starts_at: '2026-09-16T16:00:00Z'});

// The bug: a call with a start was dressed as a calendar entry.
check('call is a call', L.styleOf(claudeCall), 'call');
check('calendar reminder is an event', L.styleOf(calendar), 'event');
// Calendar dress needs both the category and a start it can place.
check('event without start is generic', L.styleOf(item({id: 'e', category: 'event', title: 'x'})), 'generic');
check('event with junk start is generic',
    L.styleOf(item({id: 'e', category: 'event', title: 'x', starts_at: 'tomorrow'})), 'generic');
check('event with zoneless start is generic',
    L.styleOf(item({id: 'e', category: 'event', title: 'x', starts_at: '2026-09-16T17:00:00'})), 'generic');
// Anything else with a start -- the old trigger for the date tile -- is generic.
for (const category of ['timer', 'navigation', 'transfer', 'installation', 'media', 'generic'])
    check(`${category} with a start is not an event`,
        L.styleOf(item({id: category, category, title: 'x', starts_at: '2026-09-16T16:00:00Z'})), 'generic');
check('unknown category is generic',
    L.styleOf(item({id: 'u', category: 'holo-deck', title: 'x', starts_at: '2026-09-16T16:00:00Z'})), 'generic');
check('missing category is generic',
    L.styleOf(item({id: 'u', title: 'x', starts_at: '2026-09-16T16:00:00Z'})), 'generic');
check('garbage item is generic', L.styleOf({}), 'generic');
check('unknown category icon', L.categoryIcon(item({id: 'u', category: 'nope', title: 'x'})), L.GENERIC_ICON);
check('call icon', L.categoryIcon(claudeCall), 'call-start-symbolic');

// The shelf admits an event only in its fifteen-before / ten-after window.
const now = Date.parse('2026-09-16T16:50:00Z');
check('call beats event and media', L.pickActivity([calendar, media, claudeCall], now), claudeCall);
check('call beats event (other order)', L.pickActivity([claudeCall, calendar], now), claudeCall);
check('upcoming event beats media', L.pickActivity([calendar, media], now), calendar);
check('unknown ranks last', L.pickActivity([item({id: 'u', category: 'x', title: 'x'}), calendar], now), calendar);
const soon = item({id: 's', category: 'event', title: 'soon', starts_at: '2026-09-16T16:55:00Z'});
check('earlier event first', L.pickActivity([calendar, soon], now), soon);
check('event before its window yields to media', L.pickActivity([calendar, media], Date.parse('2026-09-16T16:44:59Z')), media);
check('event at the opening boundary', L.pickActivity([calendar, media], Date.parse('2026-09-16T16:45:00Z')), calendar);
check('event at the closing boundary', L.pickActivity([calendar, media], Date.parse('2026-09-16T17:10:00Z')), calendar);
check('event after its window yields to media', L.pickActivity([calendar, media], Date.parse('2026-09-16T17:10:01Z')), media);
check('nothing live', L.pickActivity([], now), null);

// Elapsed clock.
const start = L.parseInstant('2026-09-16T16:40:12.402119+00:00');
check('parse fractional offset', start, Date.parse('2026-09-16T16:40:12.402Z'));
check('elapsed 03:23', L.elapsedText(start, start + 203_900), '03:23');
check('elapsed zero', L.elapsedText(start, start), '00:00');
check('elapsed hour', L.elapsedText(start, start + 3_725_000), '1:02:05');
check('not started yet (ringing)', L.elapsedText(start, start - 1000), null);
check('no start', L.elapsedText(null, start), null);

// Controls: a call draws only what really works, microphone first.
check('call shows only real controls',
    L.shownActions(claudeCall, claudeCall.extension.actions).map(a => a.id), ['call.mute']);
const continuity = item({
    id: 'calls.active', category: 'call', title: '+1 555 0100', subtitle: 'Phone · 12:01',
    starts_at: '2026-09-16T16:00:00Z',
    actions: [
        {id: 'call.hangup', label: 'End call', risk: 'low', enabled: true},
        {id: 'call.camera', label: 'Turn camera off', risk: 'low', enabled: false},
        {id: 'call.mute', label: 'Unmute microphone', risk: 'low', enabled: true},
    ],
});
check('phone call: mute then hang up',
    L.shownActions(continuity, continuity.extension.actions).map(a => a.id), ['call.mute', 'call.hangup']);
check('muted when offering unmute', L.microphoneMuted(continuity.extension.actions), true);
check('live when offering mute', L.microphoneMuted(claudeCall.extension.actions), false);
check('no mic control', L.microphoneMuted([]), null);
check('muted glyph', L.actionIcon(continuity.extension.actions[2]), 'luma-mic-off-symbolic');
check('live glyph', L.actionIcon(claudeCall.extension.actions[0]), 'luma-mic-symbolic');
check('hangup glyph', L.actionIcon({id: 'call.hangup'}), 'call-stop-symbolic');
check('unknown action has no glyph', L.actionIcon({id: 'agenda.open'}), null);
check('lone passive action hidden',
    L.shownActions(calendar, calendar.extension.actions), []);
const noControls = item({id: 'c', category: 'call', title: 'VoIP', starts_at: '2026-09-16T16:00:00Z'});
check('call with no controls draws none', L.shownActions(noControls, []), []);

// The kit's call subtitle, initials and identity tint.
check('call subtitle', L.callSubtitle('Muted', '0:42'), 'Muted · 0:42');
check('call subtitle, live', L.callSubtitle(null, '0:42'), '0:42');
// Studio Desktop's tile family: a recording, clock text, the event line, the key.
check('recording is its own tile', L.styleOf(item({id: 'r', category: 'recording', title: 'x', starts_at: '2026-09-16T16:00:00Z'})), 'recording');
check('clock 0:14', L.clockText(0, 14_900), '0:14');
check('clock 12:05', L.clockText(0, 725_000), '12:05');
check('clock hour', L.clockText(0, 3_725_000), '1:02:05');
check('clock before start', L.clockText(1000, 0), null);
check('event line', L.eventLine('7:30', '8:15', 480_000, 0), '7:30 – 8:15 · 8 min');
check('event line at start', L.eventLine('7:30', '8:15', 0, 20_000), '7:30 – 8:15 · now');
check('the last control is the key', [L.isKey([1, 2], 2), L.isKey([1, 2], 1), L.isKey([], 1)], [true, false, false]);
check('join glyph', L.actionIcon({id: 'event.join'}), 'camera-web-symbolic');
check('stop glyph', L.actionIcon({id: 'memo.stop'}), 'media-playback-stop-symbolic');
check('initials two words', L.initialsOf('Ada Lovelace'), 'AL');
check('initials three words', L.initialsOf('Mary Ann Evans'), 'ME');
check('initials one word', L.initialsOf('Claude'), 'CL');
check('initials number', L.initialsOf('+1 (555) 010-0142'), '42');
check('initials empty', L.initialsOf(''), '');
check('initials non-latin', L.initialsOf('émile zola'), 'ÉZ');
check('tint stable', L.tintOf('Ada Lovelace'), L.tintOf('Ada Lovelace'));
check('tint in range', [..."abcdefghij"].every(c => L.tintOf(c) >= 0 && L.tintOf(c) < L.TINT_COUNT), true);

// What luma-calls 0.1.0-1.luma.3 publishes: intent ids, mute and deafen for
// any application, no dead controls.
const discord = item({
    id: 'calls.active', category: 'call', title: 'Discord', subtitle: 'Discord · 01:00',
    starts_at: '2026-09-16T16:00:00Z',
    actions: [
        {id: 'call.deafen', label: 'Deafen', risk: 'low', enabled: true},
        {id: 'call.mute', label: 'Mute microphone', risk: 'low', enabled: true},
    ],
}, 'org.projectluma.Calls');
check('mute then deafen', L.shownActions(discord, discord.extension.actions).map(a => a.id),
    ['call.mute', 'call.deafen']);
check('live call state', L.callState(discord.extension.actions), 'live');
check('deafen glyph', L.actionIcon({id: 'call.deafen', label: 'Deafen'}), 'luma-headphones-symbolic');
check('deafen role', L.actionRole({id: 'call.deafen'}), 'deafen');
const mutedActions = [
    {id: 'call.unmute', label: 'Unmute microphone', risk: 'low', enabled: true},
    {id: 'call.deafen', label: 'Deafen', risk: 'low', enabled: true},
];
check('muted call state', L.callState(mutedActions), 'muted');
check('unmute id is muted', L.isMuted(mutedActions[0]), true);
const deafenedActions = [
    {id: 'call.unmute', label: 'Unmute microphone', risk: 'low', enabled: true},
    {id: 'call.undeafen', label: 'Undeafen', risk: 'low', enabled: true},
];
check('deafened call state', L.callState(deafenedActions), 'deafened');
check('deafened glyph', L.actionIcon(deafenedActions[1]), 'luma-headphones-off-symbolic');
check('deafen is not a microphone', L.microphoneMuted([{id: 'call.undeafen', label: 'Undeafen'}]), null);

// Two live tiles at most, the most urgent first (Studio Desktop): a call, a
// recording, an event about to start, then music, weather, toggles.
{
    const two = L.chooseLiveTiles(L.orderActivities([media, calendar, claudeCall], now), {music: true, weather: true});
    check('two tiles: the call and the event, no music', [two.publications, two.music, two.weather],
        [[claudeCall, calendar], false, false]);
    const one = L.chooseLiveTiles(L.orderActivities([calendar], now), {music: true, weather: true});
    check('event then music', [one.publications, one.music, one.weather], [[calendar], true, false]);
    const none = L.chooseLiveTiles([], {music: true, weather: true, toggles: true});
    check('music then weather', [none.publications, none.music, none.weather, none.toggles], [[], true, true, false]);
    const tight = L.chooseLiveTiles(L.orderActivities([calendar], now), {music: true}, 1);
    check('a narrow row keeps the most urgent', [tight.publications, tight.music], [[calendar], false]);
    check('no room', L.chooseLiveTiles([calendar], {music: true}, 0).publications, []);
    const early = Date.parse('2026-09-16T16:44:59Z');
    check('an event outside its window takes no tile', L.chooseLiveTiles(L.orderActivities([calendar], early), {music: true}),
        {publications: [], music: true, weather: false, toggles: false});
}

if (failures) {
    printerr(`live-activity: ${failures} failure(s)`);
    System.exit(1);
}
print('live-activity: PASS');
