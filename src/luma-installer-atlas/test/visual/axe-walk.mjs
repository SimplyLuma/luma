#!/usr/bin/env node
/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Run axe-core (WCAG rules) on every step of the mocked walk, on Paper, Ink
 * and Slate at full contrast. Needs playwright and axe-core resolvable, the
 * preview served as for capture.mjs, and PLAYWRIGHT_CHROMIUM if Playwright's
 * own browser is not installed. Exits non-zero on any violation.
 */
import fs from "node:fs";
import { createRequire } from "node:module";

import { chromium } from "playwright";

const require = createRequire(import.meta.url);
const axe = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const base = process.env.ATLAS_PREVIEW_URL || "http://127.0.0.1:8741/index.html";
const b = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM || undefined });
const results = {};
for (const surface of ["paper", "ink", "contrast"]) {
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  await p.goto(`${base}?surface=${surface}`);
  const run = async (name) => {
    await p.waitForTimeout(500);
    await p.addScriptTag({ content: axe });
    const r = await p.evaluate(async () => (await window.axe.run(document, { resultTypes: ["violations"] })).violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.slice(0,3).map(n => n.target.join(" ") + " :: " + (n.failureSummary||"").split("\n")[1]) })));
    results[`${surface}-${name}`] = r;
  };
  const primary = () => p.locator(".install-actions .install-btn.primary");
  const step = n => p.locator(".install-kicker", { hasText: `Step ${n} of 9` }).waitFor({ timeout: 15000 });
  await step(1); await p.locator(".install-option[aria-pressed='true']").first().waitFor(); await run("1");
  await primary().click(); await step(2); await run("2");
  await primary().click(); await step(3); await p.locator("#disk-method-erase-all[aria-pressed='true']").waitFor(); await run("3");
  await primary().click(); await step(4); if (!await p.locator("#encryption-lock").isChecked()) await p.locator("#encryption-lock").press("Space"); await p.fill("#encryption-passphrase", "correct horse battery"); await p.fill("#encryption-confirm", "correct horse batterx"); await run("4");
  await p.fill("#encryption-confirm", "correct horse battery"); await primary().click(); await step(5);
  await p.fill("#account-name", "Sam"); await p.fill("#account-password", "a long password"); await p.fill("#account-confirm", "a long password"); await p.waitForTimeout(400); await run("5");
  await primary().click(); await step(6); await p.locator("#apps-studio").click(); await run("6");
  await primary().click(); await step(7); await p.locator(".install-summary").waitFor(); await run("7");
  await primary().click(); await step(8); await p.waitForTimeout(3000); await run("8");
  await step(9); await run("9");
  await p.close();
}
await b.close();
let violations = 0;
for (const [k, v] of Object.entries(results)) {
    violations += v.length;
    console.log(k, v.length ? JSON.stringify(v) : "no violations");
}
process.exit(violations ? 1 : 0);
