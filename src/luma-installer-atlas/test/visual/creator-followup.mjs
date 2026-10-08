#!/usr/bin/env node
/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Real browser interaction against the explicit Atlas mock backend. This is
 * UI/API forwarding evidence, not a real disk installation or boot proof.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import { chromium } from "playwright";
const base = process.env.ATLAS_PREVIEW_URL || "http://127.0.0.1:8741/index.html";
const output = process.env.ATLAS_FOLLOWUP_OUTPUT || "/tmp/luma-atlas-followup";
fs.mkdirSync(output, { recursive: true });
const browser = process.env.ATLAS_CDP_URL
    ? await chromium.connectOverCDP(process.env.ATLAS_CDP_URL)
    : await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM || undefined });
const step = (p, n) => p.locator(".install-kicker", { hasText: `Step ${n} of 9` }).waitFor();
const next = p => p.locator(".install-actions .install-btn.primary").click();
try {
    for (const viewport of [{ width: 1024, height: 640 }, { width: 1440, height: 900 }]) {
        const context = await browser.newContext({ viewport });
        const p = await context.newPage();
        const errors = []; p.on("pageerror", e => errors.push(e.message));
        await p.goto(`${base}?surface=paper&backendDelay=1800`);
        await p.locator(".install-startup img").waitFor();
        const r = await p.locator(".install-startup img").boundingBox();
        assert.equal(r.width, 142); assert.equal(r.height, 75);
        assert.equal(r.x, (viewport.width - 142) / 2);
        assert.equal(r.y, (viewport.height - 75) / 2);
        assert.equal(await p.evaluate(() => window.__atlasMockHeartbeats || 0), 0);
        const status = await p.locator(".install-startup .install-visually-hidden").boundingBox();
        assert.ok(status.width <= 1 && status.height <= 1);
        await p.screenshot({ path: `${output}/startup-${viewport.width}.png` });
        await step(p, 1); await p.waitForFunction(() => window.__atlasMockHeartbeats > 0);
        await p.locator(".install-option[aria-pressed='true']").first().waitFor();
        await next(p); await step(p, 2); await next(p); await step(p, 3);
        await p.locator("#disk-method-erase-all[aria-pressed='true']").waitFor();
        await next(p); await step(p, 4);
        assert.equal(await p.locator("#encryption-lock").isChecked(), false);
        assert.equal(await p.locator("#encryption-passphrase").count(), 0);
        await next(p); await step(p, 5);
        assert.equal(await p.evaluate(() => window.__atlasMockEncryptionRequests.at(-1)), false);
        await p.locator("#account-password").fill("a long password");
        await p.locator("#account-password").press("Tab");
        assert.equal(await p.evaluate(() => document.activeElement.id), "account-confirm");
        await p.locator("#account-password").focus();
        await p.locator("#account-password").press("Alt+r");
        assert.equal(await p.locator("#account-password").getAttribute("type"), "text");
        await p.locator(".install-reveal[aria-controls='account-password']").click();
        assert.equal(await p.locator("#account-password").getAttribute("type"), "password");
        await p.locator(".install-actions .install-btn.quiet").click(); await step(p, 4);
        if (!await p.locator("#encryption-lock").isChecked()) await p.locator("#encryption-lock").press("Space");
        await p.locator("#encryption-passphrase").fill("correct horse battery");
        await p.locator("#encryption-passphrase").press("Tab");
        assert.equal(await p.evaluate(() => document.activeElement.id), "encryption-confirm");
        await p.locator("#encryption-confirm").fill("correct horse battery");
        await p.waitForTimeout(400); await next(p); await step(p, 5);
        assert.equal(await p.evaluate(() => window.__atlasMockEncryptionRequests.at(-1)), true);
        await p.locator(".install-actions .install-btn.quiet").click(); await step(p, 4);
        assert.equal(await p.locator("#encryption-lock").isChecked(), true);
        await next(p); await step(p, 5);
        await p.locator("#account-name").fill("Sam");
        await p.locator("#account-password").fill("a long password");
        await p.locator("#account-confirm").fill("a long password");
        await p.waitForTimeout(400); await next(p); await step(p, 6);
        assert.equal(await p.getByText("Nothing extra will be downloaded.", { exact: false }).count(), 0);
        await p.getByText("You can always download these from the Depot at a later time.", { exact: true }).waitFor();
        await p.screenshot({ path: `${output}/apps-${viewport.width}.png` });
        await p.locator(".install-rail button").nth(3).click(); await step(p, 4);
        await p.reload(); await step(p, 4);
        assert.equal(await p.locator("#encryption-lock").isChecked(), true);
        assert.equal(await p.locator("#encryption-passphrase").inputValue(), "");
        assert.deepEqual(errors, []);
        await context.close();
    }
    console.log("PASS startup ready-frame, unlocked default/explicit opt-in, preserved choice, secret-field focus/reveal and apps copy at both viewports");
} finally { await browser.close(); }
