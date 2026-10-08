/* SPDX-License-Identifier: LGPL-2.1-or-later */
import assert from "node:assert/strict";
import { test } from "node:test";
import { installerAppearance } from "../../src/model/appearance.js";

test("fresh installer starts in Ink and respects an explicit light environment", () => {
    assert.deepEqual(installerAppearance(), { contrast: null, surface: "ink" });
    assert.deepEqual(installerAppearance({ light: true }), { contrast: null, surface: "paper" });
    assert.deepEqual(installerAppearance({ light: true, contrast: true }), { contrast: "more", surface: "slate" });
});

test("URL and boot choices retain their precedence over the environment", () => {
    assert.deepEqual(installerAppearance({ override: "paper", bootOptions: { surface: "ink" }, contrast: true }),
        { contrast: null, surface: "paper" });
    assert.deepEqual(installerAppearance({ override: "contrast", light: true }), { contrast: "more", surface: "slate" });
    assert.deepEqual(installerAppearance({ bootOptions: { surface: "slate", contrast: true }, light: true }),
        { contrast: "more", surface: "slate" });
    assert.deepEqual(installerAppearance({ override: "invalid" }), { contrast: null, surface: "ink" });
});
