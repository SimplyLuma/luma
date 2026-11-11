// SPDX-License-Identifier: Apache-2.0
// The cellular state the status area draws, from ModemManager's view of a
// modem: its state, why it failed, its SIM and its 3GPP registration. A modem
// is registered long before, and whether or not, a data connection uses it,
// so none of this asks NetworkManager. Pure (no GI imports): the mapping is
// tested without a modem.

// MMModemState.
export const ModemState = {
    FAILED: -1, UNKNOWN: 0, INITIALIZING: 1, LOCKED: 2, DISABLED: 3,
    DISABLING: 4, ENABLING: 5, ENABLED: 6, SEARCHING: 7, REGISTERED: 8,
    DISCONNECTING: 9, CONNECTING: 10, CONNECTED: 11,
};

// MMModemStateFailedReason.
export const FailedReason = {
    NONE: 0, UNKNOWN: 1, SIM_MISSING: 2, SIM_ERROR: 3,
    UNKNOWN_CAPABILITIES: 4, ESIM_WITHOUT_PROFILES: 5,
};

// MMModem3gppRegistrationState.
export const Registration = {
    IDLE: 0, HOME: 1, SEARCHING: 2, DENIED: 3, UNKNOWN: 4, ROAMING: 5,
    HOME_SMS_ONLY: 6, ROAMING_SMS_ONLY: 7, EMERGENCY_ONLY: 8,
    HOME_CSFB_NOT_PREFERRED: 9, ROAMING_CSFB_NOT_PREFERRED: 10,
    ATTACHED_RLOS: 11,
};

export const NO_SERVICE_ICON = 'luma-cellular-no-service-symbolic';
export const NO_SIM_ICON = 'luma-cellular-no-sim-symbolic';

const NO_SIM_REASONS = new Set([FailedReason.SIM_MISSING,
    FailedReason.SIM_ERROR, FailedReason.ESIM_WITHOUT_PROFILES]);
// On a network that carries the line; emergency-only and RLOS are not service.
const REGISTERED = new Set([Registration.HOME, Registration.ROAMING,
    Registration.HOME_SMS_ONLY, Registration.ROAMING_SMS_ONLY,
    Registration.HOME_CSFB_NOT_PREFERRED,
    Registration.ROAMING_CSFB_NOT_PREFERRED]);

// GNOME's own steps (signalToIcon in status/network.js).
export function signalLevel(quality) {
    if (!(quality >= 20))
        return 'none';
    if (quality < 40)
        return 'weak';
    if (quality < 50)
        return 'ok';
    return quality < 80 ? 'good' : 'excellent';
}

// One modem: {state, failedReason, sim, registration, quality}, each as
// ModemManager publishes it (sim is the SIM object path, '/' for none;
// registration is null for a modem without the 3GPP interface).
// Returns {kind: 'signal' | 'no-service' | 'no-sim', level, icon}.
export function modemStatus({state, failedReason = FailedReason.NONE,
    sim = null, registration = null, quality = 0} = {}) {
    const noSim = {kind: 'no-sim', level: null, icon: NO_SIM_ICON};
    const noService = {kind: 'no-service', level: null, icon: NO_SERVICE_ICON};

    if (state === ModemState.FAILED)
        return NO_SIM_REASONS.has(failedReason) ? noSim : noService;

    // A searching modem still reports the last signal it measured, so the
    // signal is only drawn once the modem is on a network.
    const registered = state >= ModemState.ENABLED &&
        (registration === null
            ? state >= ModemState.REGISTERED
            : REGISTERED.has(registration));
    if (registered) {
        const level = signalLevel(quality);
        return {kind: 'signal', level,
            icon: `network-cellular-signal-${level}-symbolic`};
    }

    // While a modem starts it has not read its SIM yet.
    if (sim === '/' && state >= ModemState.DISABLED)
        return noSim;
    return noService;
}

const RANK = {'signal': 2, 'no-service': 1, 'no-sim': 0};

// The device's state: its best modem's, or null without a modem.
export function cellularStatus(modems) {
    let best = null;
    for (const modem of modems ?? []) {
        const status = modemStatus(modem);
        if (!best || RANK[status.kind] > RANK[best.kind])
            best = status;
    }
    return best;
}
