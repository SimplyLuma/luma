/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * §7: low battery on a laptop before the review step.
 */

export const LOW_BATTERY_PERCENT = 30;

/**
 * @param {{present: boolean, capacity: number|null, charging: boolean, acOnline: boolean}|null} battery
 * @param {string} [duration] the step 7 promise, as a clause (model/delivery.js durationClause)
 * @returns {{ block: boolean, strong: string, plain: string }|null}
 */
export const batteryAdvice = (battery, duration = "and this usually takes under ten minutes") => {
    if (!battery || !battery.present || battery.acOnline || battery.charging) {
        return null;
    }
    if (typeof battery.capacity !== "number" || battery.capacity >= LOW_BATTERY_PERCENT) {
        return null;
    }
    return {
        block: true,
        plain: `The battery is at ${battery.capacity}%, ${duration}.`,
        strong: "Plug in first.",
    };
};
