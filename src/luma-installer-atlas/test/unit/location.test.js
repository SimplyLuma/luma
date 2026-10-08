// SPDX-License-Identifier: LGPL-2.1-or-later
import test from "node:test";
import assert from "node:assert/strict";
import { confirmLocation } from "../../src/model/location.js";

test("city confirmation waits for backend readback and durable intent", async () => {
    let readback, written;
    const trace = [];
    const result = confirmLocation({ expected: "Asia/Kathmandu",
        getTimezone: () => new Promise(resolve => { readback = resolve; }),
        record: zone => { trace.push(zone); return new Promise(resolve => { written = resolve; }); }
    });
    let completed = false; result.then(() => { completed = true; });
    await Promise.resolve(); assert.equal(completed, false); assert.deepEqual(trace, []);
    readback("Asia/Kathmandu"); await Promise.resolve();
    assert.deepEqual(trace, ["Asia/Kathmandu"]); assert.equal(completed, false);
    written({ ok: true, timezone: "Asia/Kathmandu" });
    assert.equal(await result, "Asia/Kathmandu");
});

test("stale zone, missing intent, and failed writes never permit continuation", async () => {
    let writes = 0;
    await assert.rejects(confirmLocation({ expected: "Europe/Paris", getTimezone: async () => "UTC",
        record: async () => { writes++; } }), /readback/);
    assert.equal(writes, 0);
    for (const record of [async () => ({ ok: false }), async () => ({ ok: true, timezone: "UTC" }),
        async () => { throw new Error("write denied"); }]) {
        await assert.rejects(confirmLocation({ expected: "Europe/Paris", getTimezone: async () => "Europe/Paris", record }));
    }
});
