#!/usr/bin/env node
/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Walk Atlas through all eight steps with real clicks and key presses against
 * the mocked backend (dist-preview/), capturing each step on every surface at
 * 1440×900 and 1024×640, then check the acceptance points that a browser can
 * check: the column holds at 640px, the dots stay on screen, every dot has an
 * accessible name, a wrong second passphrase blocks Continue with a message
 * under the field, and no copy contains an em dash.
 *
 *   ATLAS_PREVIEW=1 node build.js
 *   python3 -m http.server 8741 --bind 127.0.0.1 --directory dist-preview &
 *   PLAYWRIGHT_CHROMIUM=/path/to/chrome node test/visual/capture.mjs [outdir]
 *
 * Screenshots from this harness show MOCK data. They verify layout, copy and
 * behaviour, not that a real installer produced the values.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const here = path.dirname(fileURLToPath(import.meta.url));
const outdir = path.resolve(process.argv[2] || path.join(here, "output"));
const base = process.env.ATLAS_PREVIEW_URL || "http://127.0.0.1:8741/index.html";
fs.mkdirSync(outdir, { recursive: true });

const failures = [];
const check = (condition, message) => {
    if (!condition) {
        failures.push(message);
        console.log(`  FAIL ${message}`);
    }
};

const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM || undefined });

const waitStep = (page, n) => page.locator(".install-kicker", { hasText: `Step ${n} of 9` }).waitFor({ timeout: 15000 });
const primary = (page) => page.locator(".install-actions .install-btn.primary");

const layoutChecks = async (page, label, viewport) => {
    const facts = await page.evaluate(() => {
        const panel = document.querySelector(".install-panel");
        const dots = [...document.querySelectorAll(".install-rail button")];
        const rail = document.querySelector(".install-rail")?.getBoundingClientRect();
        return {
            dashes: /—/.test(document.body.innerText),
            dotNames: dots.map(dot => dot.querySelector(".install-visually-hidden")?.textContent || ""),
            dotsHidden: dots.map(dot => getComputedStyle(dot.querySelector(".install-visually-hidden")).display),
            panelWidth: panel?.getBoundingClientRect().width,
            railBottom: rail ? rail.bottom : null,
            railTop: rail ? rail.top : null,
        };
    });
    check(Math.round(facts.panelWidth) === 640, `${label}: column is ${facts.panelWidth}px, expected 640`);
    check(facts.railTop !== null && facts.railTop >= 0 && facts.railBottom <= viewport.height, `${label}: dots off screen (${facts.railTop}-${facts.railBottom})`);
    check(facts.dotNames.length === 9 && facts.dotNames.every(name => /^Step \d, .+, (current|completed|not reached yet)$/.test(name)), `${label}: dot names ${JSON.stringify(facts.dotNames)}`);
    check(facts.dotsHidden.every(value => value !== "none"), `${label}: a dot label uses display:none`);
    check(!facts.dashes, `${label}: an em dash appears in the copy`);
};

const walk = async ({ surface, viewport }) => {
    const tag = `${viewport.width}x${viewport.height}-${surface}`;
    const context = await browser.newContext({ deviceScaleFactor: 1, viewport });
    const page = await context.newPage();
    page.on("pageerror", error => failures.push(`${tag}: page error ${error.message}`));
    const shot = async (name) => {
        await page.waitForTimeout(350);
        await page.screenshot({ path: path.join(outdir, `${name}-${tag}.png`) });
    };

    await page.goto(`${base}?surface=${surface}&speed=1&ejectDelay=2500`);

    await waitStep(page, 1);
    await page.locator(".install-option[aria-pressed='true']").first().waitFor();
    check(await page.locator(".install-option-tag", { hasText: "Detected" }).count() === 1, `${tag}: Detected tag missing on firmware-detected language`);
    await layoutChecks(page, `${tag} step 1`, viewport);
    await shot("01-language");
    await primary(page).click();

    await waitStep(page, 2);
    await page.locator(".install-note", { hasText: "Your clock will read" }).waitFor();
    await layoutChecks(page, `${tag} step 2`, viewport);
    await shot("02-location");
    await page.keyboard.press("ArrowRight");

    await waitStep(page, 3);
    await page.locator("#disk-method-erase-all[aria-pressed='true']").waitFor({ timeout: 15000 });
    check(await page.locator("#disk-sdb[aria-disabled='true']").count() === 1, `${tag}: installer USB is not disabled`);
    await layoutChecks(page, `${tag} step 3`, viewport);
    await shot("03-disk");
    await primary(page).click();

    await waitStep(page, 4);
    check(!await page.locator("#encryption-lock").isChecked(), `${tag}: Lock this drive is not off by default`);
    if (!await page.locator("#encryption-lock").isChecked()) await page.locator("#encryption-lock").press("Space");
    if (!await page.locator("#encryption-lock").isChecked()) await page.locator("#encryption-lock").press("Space");
    await page.fill("#encryption-passphrase", "correct horse battery");
    await page.fill("#encryption-confirm", "correct horse batterx");
    await page.waitForTimeout(200);
    check(await primary(page).getAttribute("aria-disabled") === "true", `${tag}: Continue not blocked by a wrong second passphrase`);
    check((await page.locator("#encryption-confirm-problem").textContent())?.includes("These do not match."), `${tag}: mismatch message missing under the field`);
    check(await page.locator("[role='dialog'], dialog").count() === 0, `${tag}: a dialog appeared`);
    if (surface === "paper" && viewport.width === 1440) {
        await shot("04b-encryption-mismatch");
    }
    await page.fill("#encryption-confirm", "correct horse battery");
    await layoutChecks(page, `${tag} step 4`, viewport);
    await shot("04-encryption");
    await primary(page).click();

    await waitStep(page, 5);
    await page.fill("#account-name", "Sam Rivera");
    await page.waitForTimeout(500);
    check(await page.inputValue("#account-username") === "sam", `${tag}: username not derived from the name`);
    await page.fill("#account-password", "a long password");
    await page.fill("#account-confirm", "a long password");
    await layoutChecks(page, `${tag} step 5`, viewport);
    await shot("05-account");
    await primary(page).click();

    await waitStep(page, 6);
    check(await page.locator("[id^='apps-'][aria-pressed='true']").count() === 0, `${tag}: an app collection is preselected`);
    check((await page.locator(".install-lede").textContent()).includes("internet connection"), `${tag}: apps step does not say downloads need an internet connection`);
    await page.locator("#apps-office").click();
    await page.locator("#apps-creative").click();
    await layoutChecks(page, `${tag} step 6`, viewport);
    await shot("06-apps");
    await primary(page).click();

    await waitStep(page, 7);
    await page.locator(".install-summary").waitFor({ timeout: 15000 });
    const layout = await page.locator("#summary-layout + dd").textContent();
    check(!/swap \d/.test(layout), `${tag}: a swap partition appears in the layout line: ${layout}`);
    check(/subvolume/.test(layout), `${tag}: btrfs subvolumes missing from the layout line: ${layout}`);
    const warn = await page.locator(".install-warn").first().textContent();
    check(/erased/.test(warn) && /will not be touched/.test(warn), `${tag}: review warning does not name both drives: ${warn}`);
    check(await page.locator(".install-summary-link").count() >= 7, `${tag}: review rows are not links`);
    check((await page.locator("#summary-apps + dd").textContent()).includes("Office and Creative"), `${tag}: review does not show the chosen apps`);
    check((await page.locator("#summary-installing + dd").textContent()) === "Luma (Version 1, Prairie) · Stable · from this USB drive", `${tag}: review does not say what is installed and from where`);
    await layoutChecks(page, `${tag} step 7`, viewport);
    await shot("07-review");
    // Reload survival needs a backend that outlives the page: see vm-walk.mjs.

    // Dot names show on hover (handoff §6).
    if (surface === "paper" && viewport.width === 1440) {
        await page.locator(".install-rail button").nth(2).hover();
        await page.waitForTimeout(250);
        const opacity = await page.locator(".install-rail button").nth(2).locator(".install-rail-name").evaluate(el => getComputedStyle(el).opacity);
        check(Number(opacity) > 0.9, `${tag}: dot name not shown on hover`);
        await shot("07b-review-dot-hover");
        await page.mouse.move(0, 0);
    }

    await primary(page).click();
    await waitStep(page, 8);
    await page.locator(".install-phase[data-state='doing']").nth(0).waitFor();
    check(!(await page.locator(".install-lede").textContent()).includes("close the lid"), `${tag}: removed lid claim is visible`);
    await page.waitForFunction(() => document.querySelector(".install-percent")?.textContent === "100%", null, { timeout: 30000 });
    await layoutChecks(page, `${tag} step 8`, viewport);
    await shot("08-installing");

    await waitStep(page, 9);
    await layoutChecks(page, `${tag} step 9`, viewport);
    await shot("09-done");
    await context.close();
};

const extras = async () => {
    const viewport = { height: 900, width: 1440 };
    const context = await browser.newContext({ deviceScaleFactor: 1, viewport });
    const page = await context.newPage();
    // Atlas restores its place after a reload; each scenario starts clean.
    const fresh = async (query) => {
        await page.goto(`${base}?blank`);
        await page.evaluate(() => window.sessionStorage.clear());
        await page.goto(`${base}?${query}`);
    };

    // No firmware language: nothing is called Detected.
    await fresh("surface=paper&platformLang=");
    await waitStep(page, 1);
    await page.locator(".install-option[aria-pressed='true']").first().waitFor();
    check(await page.locator(".install-option-tag").count() === 0, "no-detection: a Detected tag appeared without detection");

    // Without the inhibitor, the lid sentence is not said; failure copy and log saving.
    for (const [query, name] of [["surface=contrast&inhibitor=0&install=fail&speed=0.4", "08c-installing-failed-contrast"], ["surface=ink&inhibitor=0&install=fail&logs=none&speed=0.4", "08e-installing-failed-no-log-drive"], ["surface=slate&speed=0.3&ejectDelay=8000", "09c-done-slate"]]) {
        await fresh(query);
        await waitStep(page, 1);
        await page.locator(".install-option[aria-pressed='true']").first().waitFor();
        await primary(page).click();
        await waitStep(page, 2);
        await primary(page).click();
        await waitStep(page, 3);
        await page.locator("#disk-method-erase-all[aria-pressed='true']").waitFor({ timeout: 15000 });
        await primary(page).click();
        await waitStep(page, 4);
        if (!await page.locator("#encryption-lock").isChecked()) await page.locator("#encryption-lock").press("Space");
        await page.fill("#encryption-passphrase", "correct horse battery");
        await page.fill("#encryption-confirm", "correct horse battery");
        await primary(page).click();
        await waitStep(page, 5);
        await page.fill("#account-name", "Sam");
        await page.fill("#account-password", "a long password");
        await page.fill("#account-confirm", "a long password");
        await page.waitForTimeout(400);
        await primary(page).click();
        await waitStep(page, 6);
        await primary(page).click();
        await waitStep(page, 7);
        await page.locator(".install-summary").waitFor({ timeout: 15000 });
        await primary(page).click();
        await waitStep(page, 8);
        if (query.includes("install=fail")) {
            check(!(await page.locator(".install-lede").textContent()).includes("close the lid"), "no-inhibitor: lid sentence shown without an inhibitor");
            await page.locator(".install-warn", { hasText: "Something went wrong" }).waitFor({ timeout: 30000 });
            const details = page.getByRole("button", { name: "Details", exact: true });
            check(await details.getAttribute("aria-expanded") === "false", "failure: details start collapsed");
            await details.click();
            const detailText = page.locator("#install-error-details");
            await detailText.getByText("Failed to pull payload: Writing object: No space left on device", { exact: true }).waitFor();
            check(await page.getByRole("button", { name: "Hide details", exact: true }).getAttribute("aria-expanded") === "true", "failure: details expansion is accessible");
            check(await page.getByRole("button", { name: "Restart", exact: true }).count() === 1, "failure: Restart is retained");
            await page.locator(".install-btn", { hasText: "Save the log" }).click();
            await page.locator(".install-note", { hasText: query.includes("logs=none") ? "Plug in a USB drive" : "Saved to" }).waitFor();
            check(await detailText.innerText() === "Failed to pull payload: Writing object: No space left on device", "failure: error details remain available without a log drive");
            await page.waitForTimeout(300);
            await page.screenshot({ path: path.join(outdir, `${name}-1440x900.png`) });
            const primaryFailure = await page.locator(".install-warn").innerText();
            await page.reload();
            await page.locator(".install-warn", { hasText: "Something went wrong" }).waitFor();
            check(await page.locator(".install-warn").innerText() === primaryFailure, "failure: its phase survives reload");
            await page.getByRole("button", { name: "Details", exact: true }).click();
            await page.locator("#install-error-details").getByText("Failed to pull payload: Writing object: No space left on device", { exact: true }).waitFor();
            check(await page.locator(".install-kicker").textContent() === "Step 8 of 9", "failure: reload never advances to installed/eject success");
        } else {
            await waitStep(page, 9);
            await page.waitForTimeout(300);
            await page.screenshot({ path: path.join(outdir, `${name}-1440x900.png`) });
        }
    }

    const toReview = async () => {
        await waitStep(page, 1);
        await page.locator(".install-option[aria-pressed='true']").first().waitFor();
        for (let step = 1; step <= 6; step++) {
            if (step === 4) {
                if (!await page.locator("#encryption-lock").isChecked()) await page.locator("#encryption-lock").press("Space");
                await page.fill("#encryption-passphrase", "correct horse battery");
                await page.fill("#encryption-confirm", "correct horse battery");
            }
            if (step === 5) {
                await page.fill("#account-name", "Sam");
                await page.fill("#account-password", "a long password");
                await page.fill("#account-confirm", "a long password");
                await page.waitForTimeout(400);
            }
            if (step === 3) {
                await page.locator("#disk-method-erase-all[aria-pressed='true']").waitFor({ timeout: 15000 });
            }
            await primary(page).click();
            await waitStep(page, step + 1);
        }
        await page.locator(".install-summary").waitFor({ timeout: 15000 });
    };

    // A saved nonrunning task and Stopped arrive together. Native Finish
    // consumes its error, so both readers must share one rejected completion.
    await fresh("surface=ink&install=fail&resumeFailure=1&logs=none");
    await page.evaluate(() => sessionStorage.setItem("luma-atlas-session", JSON.stringify({
        current: 7, reached: 7, installTask: "/org/fedoraproject/Anaconda/Task/1",
    })));
    await page.reload();
    await waitStep(page, 8);
    await page.locator(".install-warn", { hasText: "Something went wrong" }).waitFor({ timeout: 15000 });
    await page.getByRole("button", { name: "Details", exact: true }).click();
    await page.locator("#install-error-details", { hasText: "No space left on device" }).waitFor();
    await page.waitForTimeout(500);
    check(await page.evaluate(() => window.__atlasMockFinishCalls) === 1, "resume/stopped: Finish invoked exactly once");
    check(await page.locator(".install-kicker").textContent() === "Step 8 of 9", "resume/stopped: rejected completion never advances to success");
    await page.screenshot({ path: path.join(outdir, "08f-installing-resume-stopped-race-1440x900.png") });

    // Low battery blocks the erase button on review.
    await fresh("surface=paper&battery=12");
    await toReview();
    await page.locator(".install-warn", { hasText: "Plug in first." }).waitFor({ timeout: 15000 });
    check(await primary(page).getAttribute("aria-disabled") === "true", "low-battery: erase button not blocked");
    await page.screenshot({ path: path.join(outdir, "07c-review-low-battery-1440x900.png") });

    // An online install without the internet cannot start (ADR-030 §9).
    await fresh("surface=paper&source=online&net=none");
    await toReview();
    await page.locator(".install-warn", { hasText: "Connect to the internet first." }).waitFor({ timeout: 15000 });
    check(await primary(page).getAttribute("aria-disabled") === "true", "online-offline: install button not blocked without the internet");
    check((await page.locator("#summary-installing + dd").textContent()) === "Luma · Stable · downloaded as it installs", "online-offline: review does not say Luma is downloaded");
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(outdir, "07d-review-online-no-internet-1440x900.png") });

    // Online with the internet: the duration depends on the connection.
    await fresh("surface=ink&source=online&net=full&speed=0.5");
    await toReview();
    check(await primary(page).getAttribute("aria-disabled") !== "true", "online: install button blocked with the internet");
    await primary(page).click();
    await waitStep(page, 8);
    await page.waitForFunction(() => document.querySelector(".install-lede")?.textContent.startsWith("How long this takes depends on your internet connection."), null, { timeout: 5000 })
            .catch(() => failures.push("online: installing lede does not say the duration depends on the connection"));
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(outdir, "08d-installing-online-ink-1440x900.png") });

    // Staff media on the nightly channel: the last step says how to enroll.
    await fresh("surface=paper&channel=nightly&speed=0.3&ejectDelay=600");
    await toReview();
    check((await page.locator("#summary-installing + dd").textContent()) === "Luma (Prairie, Beta 0, Nightly 20260916) · from this USB drive", "nightly: review does not name the release");
    await primary(page).click();
    await waitStep(page, 9);
    const doneLede = await page.locator(".install-lede").textContent().catch(() => "");
    check(doneLede.startsWith("Luma (Prairie, Beta 0, Nightly 20260916) is installed. "), `nightly: last step does not name the release: ${doneLede}`);
    const enroll = await page.locator(".install-note").textContent().catch(() => "");
    check(enroll.includes("luma-update enroll-preview nightly") && !/—/.test(enroll), `nightly: enrollment note missing or malformed: ${enroll}`);
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(outdir, "09d-done-nightly-1440x900.png") });

    // Staff media that carry the batch's preview credential enroll the computer: no note.
    await fresh("surface=paper&channel=nightly&enrolled=1&speed=0.3&ejectDelay=600");
    await toReview();
    await primary(page).click();
    await waitStep(page, 9);
    check(await page.locator(".install-note").count() === 0, "nightly enrolled: an enrollment note appeared although the medium enrolled the computer");

    // Offline installation keeps automatic time for the first connection.
    await fresh("surface=ink&offline=1");
    await waitStep(page, 1);
    await page.locator(".install-option[aria-pressed='true']").first().waitFor();
    await primary(page).click();
    await waitStep(page, 2);
    await page.waitForFunction(() => document.querySelector("#location-auto-time")?.checked);
    check(await page.locator("#location-auto-time").isEnabled(), "offline: automatic time preference cannot be changed");
    check(await page.locator("#location-date").count() === 0, "offline: manual date shown while automatic time is on");
    await page.locator("#location-auto-time").uncheck();
    await page.locator("#location-date").waitFor();
    check(!(await page.locator("#location-auto-time").isChecked()), "offline: explicit manual choice was overwritten");
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(outdir, "02c-location-offline-ink-1440x900.png") });

    await context.close();
};

const viewports = [{ height: 900, width: 1440 }, { height: 640, width: 1024 }];
for (const viewport of viewports) {
    for (const surface of ["paper", "ink"]) {
        console.log(`walking ${viewport.width}x${viewport.height} ${surface}`);
        await walk({ surface, viewport });
    }
}
console.log("extra states");
await extras();
await browser.close();

console.log(failures.length ? `\n${failures.length} check(s) failed` : "\nall checks passed");
process.exit(failures.length ? 1 : 0);
