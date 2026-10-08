/* SPDX-License-Identifier: LGPL-2.1-or-later */
import assert from "node:assert/strict";
import { test } from "node:test";
import { taskCompletion } from "../../src/model/task-finish.js";

test("concurrent stopped and resumed readers cannot consume a failure twice", async () => {
    const failure = new Error("Failed to pull payload: No space left on device");
    let calls = 0;
    // Native44 raises a stopped task's stored error once, then removes it.
    const task = { Finish: async () => { if (++calls === 1) throw failure } };
    const complete = taskCompletion(task);
    const [stopped, resumed] = await Promise.allSettled([complete(), complete()]);
    assert.equal(calls, 1);
    assert.equal(stopped.status, "rejected");
    assert.equal(resumed.status, "rejected");
    assert.equal(stopped.reason, failure);
    assert.equal(resumed.reason, failure);
    await assert.rejects(complete(), error => error === failure);
    assert.equal(calls, 1);
});

test("success is shared and a synchronous transport error remains a rejection", async () => {
    let calls = 0;
    const complete = taskCompletion({ Finish: () => ++calls });
    assert.deepEqual(await Promise.all([complete(), complete()]), [1, 1]);
    const failed = taskCompletion({ Finish: () => { throw new Error("D-Bus connection closed") } });
    await assert.rejects(failed(), /D-Bus connection closed/);
});
