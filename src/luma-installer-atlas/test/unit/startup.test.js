/* SPDX-License-Identifier: LGPL-2.1-or-later */
import assert from "node:assert/strict";
import { test } from "node:test";
import { startupLockupSvg } from "../../src/model/startup.js";

test("startup uses the native centered lockup geometry and canonical artwork", () => {
    const source = '<svg viewBox="0 0 2219 715" width="2219" height="715"><path fill="currentColor" d="canonical"/></svg>';
    const svg = startupLockupSvg(source);
    assert.match(svg, /viewBox="0 0 142 75" width="142" height="75"/);
    assert.match(svg, /width="142" height="45"/);
    assert.match(svg, /d="canonical"/);
    assert.equal((svg.match(/<rect /g) || []).length, 4);
    assert.match(svg, /x="86" y="69" width="6" height="6"/);
    assert.throws(() => startupLockupSvg('<svg viewBox="0 0 20 20"/>'), /Unsupported canonical/);
});
