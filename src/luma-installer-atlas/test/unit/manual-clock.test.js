// Copyright (C) 2026 Project Luma contributors
// SPDX-License-Identifier: LGPL-2.1-or-later
import test from "node:test";
import assert from "node:assert/strict";
import { confirmManualClock, isoInZone } from "../../src/model/clock.js";

const draft = { date: "2026-10-06", time: "09:41", zone: "America/Los_Angeles" };
test("incomplete typing, impossible dates, invalid time and DST gaps do not write a clock", async () => {
    let writes = 0;
    for (const bad of [{ date: "202-10-06" }, { date: "2026-02-30" }, { date: "2026-13-01" }, { time: "9:41" }, { time: "24:00" }, { time: "09:6" }, { date: "2026-03-08", time: "02:30" }, { zone: "not/a-zone" }]) {
        assert.equal(isoInZone({ ...draft, ...bad }), null);
        await assert.rejects(confirmManualClock({ ...draft, ...bad, setSystemDateTime: async () => { writes++; }, getSystemDateTime: async () => "" }), /valid date/);
    }
    assert.equal(writes, 0);
});
test("leap dates, complete four-digit years and manual older dates remain valid", () => {
    assert.equal(isoInZone(draft), "2026-10-06T09:41:00-07:00");
    assert.equal(isoInZone({ date: "2024-02-29", time: "23:59", zone: "UTC" }), "2024-02-29T23:59:00+00:00");
    assert.equal(isoInZone({ ...draft, date: "2022-10-06" }), "2022-10-06T09:41:00-07:00");
});
test("manual clock completion waits for successful backend readback", async () => {
    let written, readback;
    const calls = [];
    const pending = confirmManualClock({ ...draft,
        setSystemDateTime: value => { calls.push(value); return new Promise(resolve => { written = resolve; }); },
        getSystemDateTime: () => new Promise(resolve => { readback = resolve; }),
    });
    let completed = false; pending.then(() => { completed = true; });
    assert.deepEqual(calls, [{ dateTimeSpec: "2026-10-06T09:41:00-07:00" }]);
    assert.equal(readback, undefined); written(); await Promise.resolve();
    assert.equal(completed, false);
    readback("2026-10-06T16:41:04Z");
    assert.equal(await pending, "2026-10-06T09:41:00-07:00");
});
test("failed setters and stale or malformed readbacks never acknowledge the manual choice", async () => {
    let reads = 0;
    await assert.rejects(confirmManualClock({ ...draft, setSystemDateTime: async () => { throw new Error("write denied"); }, getSystemDateTime: async () => { reads++; } }), /write denied/);
    assert.equal(reads, 0);
    for (const value of ["2026-10-06T09:41Z", "2022-10-06T16:41Z", "2026-10-06T16:42:00Z", "not-a-date", null]) {
        await assert.rejects(confirmManualClock({ ...draft, setSystemDateTime: async () => {}, getSystemDateTime: async () => value }), /did not confirm/);
    }
});
test("native Anaconda local ISO readbacks are interpreted in the chosen zone", async () => {
    assert.equal(await confirmManualClock({ ...draft, setSystemDateTime: async () => {}, getSystemDateTime: async () => "2026-10-06T09:41:02" }), "2026-10-06T09:41:00-07:00");
});
