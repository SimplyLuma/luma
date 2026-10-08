/* SPDX-License-Identifier: LGPL-2.1-or-later */
import assert from "node:assert/strict";
import { test } from "node:test";
import { restoreEncryptionChoice } from "../../src/model/encryption.js";
test("fresh and legacy sessions retain the existing unlocked default", () => {
    const initial = { storage: { luks: { encrypted: false, passphrase: "", confirmPassphrase: "" } } };
    assert.equal(restoreEncryptionChoice(initial), initial);
    assert.equal(restoreEncryptionChoice(initial, { lockDefaulted: true }), initial);
    assert.equal(restoreEncryptionChoice(initial, { lockChoice: "true" }), initial);
});
test("explicit restored choices survive without importing session secrets", () => {
    const initial = { storage: { luks: { encrypted: false, passphrase: "", confirmPassphrase: "" } } };
    for (const lockChoice of [false, true]) {
        const restored = restoreEncryptionChoice(initial, { lockChoice, passphrase: "must never restore" });
        assert.equal(restored.storage.luks.encrypted, lockChoice);
        assert.equal(restored.storage.luks.passphrase, "");
        assert.equal(restored.storage.luks.confirmPassphrase, "");
    }
    assert.equal(initial.storage.luks.encrypted, false);
});
