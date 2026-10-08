// SPDX-License-Identifier: LGPL-2.1-or-later
/** Persist only the backend's acknowledged choice, before leaving setup. */
export const confirmLocation = async ({ expected, getTimezone, record }) => {
    const actual = await getTimezone();
    if (!actual || actual !== expected) throw new Error("Time zone readback differs");
    const result = await record(actual);
    if (!result?.ok || result.timezone !== actual) throw new Error("City intent was not saved");
    return actual;
};
