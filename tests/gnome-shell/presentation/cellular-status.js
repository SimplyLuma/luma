// SPDX-License-Identifier: Apache-2.0
// The cellular indicator's state mapping, on the states ModemManager reports.
// Run: gjs -m cellular-status.js (node cellular-status.js works too).
import {
    cellularStatus, modemStatus, signalLevel,
    FailedReason, ModemState, Registration, NO_SERVICE_ICON, NO_SIM_ICON,
} from '../../../src/luma-shell-presentation/cellularStatus.js';
import {statusGlyph} from '../../../src/luma-shell-presentation/layout.js';

const out = globalThis.print ?? console.log;
function assert(value, message) { if (!value) throw Error(message); }
function is(status, kind, icon, message) {
    assert(status?.kind === kind && status?.icon === icon,
        `${message}: got ${JSON.stringify(status)}`);
}

const SIM = '/org/freedesktop/ModemManager1/SIM/0';

// No modem: nothing to draw (a desktop without one shows nothing new).
assert(cellularStatus([]) === null, 'no modem draws nothing');
assert(cellularStatus(null) === null, 'no modem list draws nothing');

// The Fairphone 6 with an inactive line: searching, yet the modem still
// reports the last signal it measured. No service, not five bars.
is(modemStatus({state: ModemState.SEARCHING, sim: SIM,
    registration: Registration.SEARCHING, quality: 100}),
'no-service', NO_SERVICE_ICON, 'searching with a stale signal is no service');

for (const registration of [Registration.IDLE, Registration.SEARCHING,
    Registration.DENIED, Registration.UNKNOWN, Registration.EMERGENCY_ONLY,
    Registration.ATTACHED_RLOS]) {
    is(modemStatus({state: ModemState.ENABLED, sim: SIM, registration, quality: 80}),
        'no-service', NO_SERVICE_ICON, `registration ${registration} is no service`);
}

// Registered: the signal, with or without a data connection.
const levels = [[0, 'none'], [19, 'none'], [20, 'weak'], [39, 'weak'],
    [40, 'ok'], [49, 'ok'], [50, 'good'], [79, 'good'], [80, 'excellent'],
    [100, 'excellent']];
for (const [quality, level] of levels) {
    assert(signalLevel(quality) === level, `${quality}% is ${level}`);
    for (const state of [ModemState.REGISTERED, ModemState.CONNECTING, ModemState.CONNECTED]) {
        is(modemStatus({state, sim: SIM, registration: Registration.HOME, quality}),
            'signal', `network-cellular-signal-${level}-symbolic`,
            `registered at ${quality}% in state ${state}`);
    }
}
assert(signalLevel(undefined) === 'none', 'an unread signal is none');
for (const registration of [Registration.HOME, Registration.ROAMING,
    Registration.HOME_SMS_ONLY, Registration.ROAMING_SMS_ONLY,
    Registration.HOME_CSFB_NOT_PREFERRED, Registration.ROAMING_CSFB_NOT_PREFERRED]) {
    is(modemStatus({state: ModemState.REGISTERED, sim: SIM, registration, quality: 60}),
        'signal', 'network-cellular-signal-good-symbolic',
        `registration ${registration} draws the signal`);
}
// A modem without the 3GPP interface is judged by its own state.
is(modemStatus({state: ModemState.CONNECTED, sim: SIM, quality: 45}),
    'signal', 'network-cellular-signal-ok-symbolic', 'non-3GPP connected modem');
is(modemStatus({state: ModemState.SEARCHING, sim: SIM, quality: 45}),
    'no-service', NO_SERVICE_ICON, 'non-3GPP searching modem');
// A registration left over on a modem that is off is not service.
is(modemStatus({state: ModemState.DISABLED, sim: SIM,
    registration: Registration.HOME, quality: 70}),
'no-service', NO_SERVICE_ICON, 'a disabled modem has no service');

// No SIM: ModemManager fails the modem, or it runs with an empty slot.
for (const failedReason of [FailedReason.SIM_MISSING, FailedReason.SIM_ERROR,
    FailedReason.ESIM_WITHOUT_PROFILES]) {
    is(modemStatus({state: ModemState.FAILED, failedReason, sim: '/'}),
        'no-sim', NO_SIM_ICON, `failed for reason ${failedReason} is no SIM`);
}
is(modemStatus({state: ModemState.FAILED, failedReason: FailedReason.UNKNOWN, sim: '/'}),
    'no-service', NO_SERVICE_ICON, 'a modem that failed for another reason is no service');
is(modemStatus({state: ModemState.ENABLED, sim: '/',
    registration: Registration.IDLE}),
'no-sim', NO_SIM_ICON, 'an enabled modem with an empty slot is no SIM');
is(modemStatus({state: ModemState.INITIALIZING, sim: '/'}),
    'no-service', NO_SERVICE_ICON, 'a starting modem has not read its SIM yet');
is(modemStatus({state: ModemState.LOCKED, sim: SIM}),
    'no-service', NO_SERVICE_ICON, 'a locked SIM is present but has no service');

// Several modems: the device shows its best one.
is(cellularStatus([
    {state: ModemState.FAILED, failedReason: FailedReason.SIM_MISSING, sim: '/'},
    {state: ModemState.SEARCHING, sim: SIM, registration: Registration.SEARCHING},
]), 'no-service', NO_SERVICE_ICON, 'a searching modem outranks an empty one');
is(cellularStatus([
    {state: ModemState.SEARCHING, sim: SIM, registration: Registration.SEARCHING},
    {state: ModemState.REGISTERED, sim: SIM, registration: Registration.ROAMING, quality: 30},
]), 'signal', 'network-cellular-signal-weak-symbolic', 'a registered modem outranks a searching one');

// The three states never share a glyph on the handheld status bar, where
// GNOME's signal names are redrawn and the Luma glyphs are left as they are.
const drawn = status => statusGlyph(status.icon) ?? status.icon;
const glyphs = new Set();
for (const [quality] of levels)
    glyphs.add(drawn(modemStatus({state: ModemState.REGISTERED, sim: SIM, registration: Registration.HOME, quality})));
assert(glyphs.size === 5, 'five signal glyphs');
for (const glyph of glyphs)
    assert(glyph.startsWith('luma-status-signal-'), `${glyph} is a signal glyph`);
assert(!glyphs.has(NO_SERVICE_ICON) && !glyphs.has(NO_SIM_ICON) &&
    NO_SERVICE_ICON !== NO_SIM_ICON, 'no service and no SIM are their own glyphs');
assert(statusGlyph(NO_SERVICE_ICON) === null && statusGlyph(NO_SIM_ICON) === null,
    'the Luma glyphs are drawn as they are');

out('cellular status ok');
