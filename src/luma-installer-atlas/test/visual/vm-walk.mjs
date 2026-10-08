#!/usr/bin/env node
/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Walk a REAL Atlas installer running in a throwaway VM (vm-test.sh remote),
 * capturing each step with the data Anaconda actually reports.
 *
 *   node test/visual/vm-walk.mjs OUTDIR review    walk steps 1-6, wait on the review step until
 *                                                  OUTDIR/go exists (checksum the drives then),
 *                                                  press the primary button, follow step 7 to 8
 *   node test/visual/vm-walk.mjs OUTDIR look      screenshot whatever is on screen
 *
 * ATLAS_URL defaults to http://127.0.0.1:18080/cockpit/@localhost/anaconda-webui/index.html
 * ATLAS_SURFACE (paper|ink) and ATLAS_TARGET (disk name, default nvme0n1).
 */
import fs from "node:fs";
import path from "node:path";

import { chromium } from "playwright";

const outdir = path.resolve(process.argv[2] || "vm-shots");
const mode = process.argv[3] || "review";
const url = process.env.ATLAS_URL || "http://127.0.0.1:18080/cockpit/@localhost/anaconda-webui/index.html";
const surface = process.env.ATLAS_SURFACE || "paper";
const target = process.env.ATLAS_TARGET || "nvme0n1";
const viewport = { height: Number(process.env.ATLAS_HEIGHT || 900), width: Number(process.env.ATLAS_WIDTH || 1440) };
fs.mkdirSync(outdir, { recursive: true });

const context = await chromium.launchPersistentContext(path.join(outdir, "profile"), {
    deviceScaleFactor: 1,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM || undefined,
    headless: true,
    viewport,
});
const page = context.pages()[0] || await context.newPage();
const log = [];
page.on("console", message => log.push(`${message.type()}: ${message.text()}`));
page.on("pageerror", error => log.push(`pageerror: ${error.message}`));

const tag = `${viewport.width}x${viewport.height}-${surface}`;
const shot = async (name) => {
    await page.waitForTimeout(600);
    const file = path.join(outdir, `vm-${name}-${tag}.png`);
    await page.screenshot({ path: file });
    console.log(`screenshot ${file}`);
};
const waitStep = (n, timeout = 120000) => page.locator(".install-kicker", { hasText: `Step ${n} of 9` }).waitFor({ timeout });
const primary = () => page.locator(".install-actions .install-btn.primary");
const facts = async () => page.evaluate(() => ({
    kicker: document.querySelector(".install-kicker")?.textContent,
    text: document.querySelector(".install-panel")?.innerText,
}));

try {
    if (mode === "look") {
        if (!page.url().startsWith("http")) {
            await page.goto(`${url}?surface=${surface}`);
        }
        await page.waitForTimeout(3000);
        await shot(`look-${Date.now()}`);
        console.log((await facts()).text);
    } else if (mode === "review") {
        await page.goto(`${url}?surface=${surface}`);
        await waitStep(1, 300000);
        await page.locator(".install-option[aria-pressed='true']").first().waitFor({ timeout: 60000 });
        await shot("01-language");
        console.log((await facts()).text);
        await primary().click();

        await waitStep(2);
        await page.locator(".install-note", { hasText: "Your clock will read" }).waitFor({ timeout: 60000 });
        await shot("02-location");
        await primary().click();

        await waitStep(3);
        await page.locator(`#disk-${target}`).waitFor({ timeout: 120000 });
        await shot("03a-disk-before-choice");
        await page.locator(`#disk-${target}`).click();
        await page.locator("#disk-method-erase-all").waitFor({ timeout: 120000 });
        await page.locator("#disk-method-erase-all").click();
        await page.locator("#disk-method-erase-all[aria-pressed='true']").waitFor();
        await shot("03-disk");
        console.log((await facts()).text);
        await primary().click();

        await waitStep(4);
        if (!await page.locator("#encryption-lock").isChecked()) await page.locator("#encryption-lock").press("Space");
        await page.fill("#encryption-passphrase", "atlas vm test passphrase");
        await page.fill("#encryption-confirm", "atlas vm test passphrase");
        await page.waitForTimeout(800);
        await shot("04-encryption");
        await primary().click();

        await waitStep(5, 300000);
        await page.fill("#account-name", "Atlas Tester");
        await page.waitForTimeout(1500);
        await page.fill("#account-password", "atlas-vm-test-pw");
        await page.fill("#account-confirm", "atlas-vm-test-pw");
        await page.locator("label[for=account-autologin]").click();
        await page.waitForTimeout(800);
        await shot("05-account");
        console.log((await facts()).text);
        await primary().click();

        await waitStep(6, 300000);
        await page.locator("#apps-office").click();
        await page.locator("#apps-studio").click();
        await shot("06-apps");
        console.log((await facts()).text);
        await primary().click();

        await waitStep(7, 300000);
        await page.locator(".install-summary").waitFor({ timeout: 300000 });
        await shot("07-review");
        console.log((await facts()).text);
        // A viewer reload must come back to the same step with Anaconda's answers.
        if (process.env.ATLAS_SKIP_RELOAD !== "1") {
        await page.reload();
        await waitStep(7, 120000);
        await page.locator(".install-summary").waitFor({ timeout: 120000 });
        await shot("07r-review-after-reload");
        console.log(`AFTER RELOAD: ${(await facts()).text.split("\n").filter(Boolean).slice(0, 3).join(" | ")}`);
        }
        await page.locator(".install-rail button").nth(2).hover();
        await page.waitForTimeout(300);
        await shot("07b-review-dot-hover");
        await page.mouse.move(0, 0);
        console.log("STOPPED AT REVIEW: create OUTDIR/go to press the primary button");
        const go = path.join(outdir, "go");
        while (!fs.existsSync(go)) {
            await page.waitForTimeout(2000);
        }
        fs.rmSync(go);
        await shot("07c-review-before-erase");
        await primary().click();
        await waitStep(8);
        await page.waitForTimeout(3000);
        await shot("08a-installing-start");
        console.log((await facts()).text);
        const started = Date.now();
        let lastPhase = "";
        for (;;) {
            const state = await page.evaluate(() => ({
                done: !!document.querySelector(".install-kicker")?.textContent.includes("Step 9"),
                failed: !!document.querySelector(".install-warn")?.textContent.includes("Something went wrong"),
                percent: document.querySelector(".install-percent")?.textContent,
                phase: [...document.querySelectorAll(".install-phase")].map(p => p.dataset.state.slice(0, 2)).join(","),
                text: document.querySelector(".install-panel")?.innerText,
            }));
            if (state.phase !== lastPhase) {
                lastPhase = state.phase;
                console.log(`${Math.round((Date.now() - started) / 1000)}s ${state.percent} phases=${state.phase}`);
                await shot(`08-installing-${state.phase}`);
            }
            if (state.done || state.failed) {
                console.log(state.text);
                break;
            }
            if (Date.now() - started > 90 * 60 * 1000) {
                console.log("TIMEOUT");
                break;
            }
            await page.waitForTimeout(5000);
        }
        await shot("09-final");
        console.log(`elapsed ${Math.round((Date.now() - started) / 1000)}s`);
        if (process.env.ATLAS_RESTART === "1") {
            await page.locator(".install-btn.primary", { hasText: "Restart now" }).click();
            console.log("PRESSED RESTART NOW");
            await page.waitForTimeout(5000);
        }
    }
} finally {
    fs.writeFileSync(path.join(outdir, `console-${mode}.log`), log.join("\n"));
    await context.close();
}
