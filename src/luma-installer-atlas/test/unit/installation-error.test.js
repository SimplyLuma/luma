/* SPDX-License-Identifier: LGPL-2.1-or-later */
import assert from "node:assert/strict";
import { test } from "node:test";
import { installationErrorDetails, missingInstallationError } from "../../src/model/installation-error.js";

test("Finish rejection preserves the authoritative backend error", () => {
    const failure = new Error("Failed to pull from repository: Writing object: No space left on device");
    failure.name = "org.fedoraproject.Anaconda.PayloadInstallationError";
    assert.equal(installationErrorDetails(failure), failure.message);
    assert.equal(installationErrorDetails(null), missingInstallationError);
    assert.equal(installationErrorDetails({ message: "  " }), missingInstallationError);
    assert.equal(installationErrorDetails({ stack: "private traceback" }), missingInstallationError);
});

test("details redact credentials while retaining the useful failure", () => {
    const message = "Could not fetch https://alice:hunter2@example.org/repo?access_token=private123&ref=luma: Permission denied; --passphrase 'a private passphrase'; password=private456; Authorization: Bearer private789";
    const detail = installationErrorDetails({ message });
    assert.ok(detail.includes("example.org/repo?access_token=[redacted]&ref=luma: Permission denied"));
    for (const secret of ["alice", "hunter2", "private123", "a private passphrase", "private456", "private789"]) {
        assert.ok(!detail.includes(secret), secret);
    }
});

test("a bounded human message never displays a full traceback or controls", () => {
    assert.equal(installationErrorDetails({ message: "Payload failed.\nTraceback (most recent call last):\n  private frame\nSecretError: value" }), "Payload failed.");
    assert.ok(!installationErrorDetails({ message: "Traceback (most recent call last):\n private frame" }).includes("private frame"));
    assert.equal(installationErrorDetails({ message: "\u001b[31mPayload failed\u001b[0m\u0000\n disk unavailable" }), "Payload failed disk unavailable");
    assert.equal(installationErrorDetails({ message: "x".repeat(3000) }).length, 2001);
});
