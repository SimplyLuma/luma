// SPDX-License-Identifier: Apache-2.0
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { deltaIdToCommit, classify, parseRef, buildIndexMaps, countEvent } from "../src/worker.js";

const HEX_A = "a".repeat(64);
const HEX_B = "0123456789abcdef".repeat(4);

function deltaId(hex) {
  const bytes = Buffer.from(hex, "hex");
  return bytes.toString("base64").replace(/=+$/, "").replace(/\//g, "_");
}

const index = {
  apps: [{
    app_id: "org.projectluma.Canvas", arch: "x86_64", branch: "beta",
    commit: HEX_B, root_dirtree: HEX_A, previous_commits: [], previous_root_dirtrees: [],
  }],
};
const maps = buildIndexMaps(index);
const noHeaders = new Headers();
const day = new Date("2026-10-01T12:00:00Z");

test("delta ids decode to commit checksums", () => {
  assert.equal(deltaIdToCommit(deltaId(HEX_B)), HEX_B);
  assert.equal(deltaIdToCommit("not base64!"), null);
});

test("classify recognises superblocks and dirtrees only", () => {
  const id = deltaId(HEX_B);
  assert.deepEqual(classify(`/repo/deltas/${id.slice(0, 2)}/${id.slice(2)}/superblock`),
    { kind: "delta", from: null, to: HEX_B });
  const from = deltaId(HEX_A);
  const both = `${from}-${id}`;
  assert.deepEqual(classify(`/repo/deltas/${both.slice(0, 2)}/${both.slice(2)}/superblock`),
    { kind: "delta", from: HEX_A, to: HEX_B });
  assert.deepEqual(classify(`/repo/objects/aa/${"a".repeat(62)}.dirtree`), { kind: "dirtree", checksum: HEX_A });
  assert.equal(classify(`/repo/objects/aa/${"a".repeat(62)}.filez`), null);
  assert.equal(classify("/repo/summary"), null);
  assert.equal(classify(`/repo/deltas/${id.slice(0, 2)}/${id.slice(2)}/0`), null);
});

test("only app refs are counted", () => {
  assert.deepEqual(parseRef("app/org.projectluma.Reel/aarch64/stable"),
    { appId: "org.projectluma.Reel", arch: "aarch64", branch: "stable" });
  assert.equal(parseRef("runtime/org.projectluma.Platform/x86_64/44"), null);
  assert.equal(parseRef("app/../x86_64/stable"), null);
  assert.equal(parseRef(null), null);
});

test("from-scratch delta is an install, from-to delta an update", () => {
  const id = deltaId(HEX_B);
  const install = countEvent({ pathname: `/repo/deltas/${id.slice(0, 2)}/${id.slice(2)}/superblock`, headers: noHeaders, maps, now: day });
  assert.deepEqual(install, { metric: "depot.install", app_id: "org.projectluma.Canvas", arch: "x86_64", branch: "beta", day: "2026-10-01", count: 1 });
  const both = `${deltaId(HEX_A)}-${id}`;
  const update = countEvent({ pathname: `/repo/deltas/${both.slice(0, 2)}/${both.slice(2)}/superblock`, headers: noHeaders, maps, now: day });
  assert.equal(update.metric, "depot.update");
});

test("root dirtree counts once, update decided by Flatpak-Upgrade-From", () => {
  const path = `/repo/objects/aa/${"a".repeat(62)}.dirtree`;
  assert.equal(countEvent({ pathname: path, headers: noHeaders, maps, now: day }).metric, "depot.install");
  const headers = new Headers({ "Flatpak-Upgrade-From": HEX_B });
  assert.equal(countEvent({ pathname: path, headers, maps, now: day }).metric, "depot.update");
  const other = `/repo/objects/bb/${"b".repeat(62)}.dirtree`;
  assert.equal(countEvent({ pathname: other, headers: noHeaders, maps, now: day }), null);
});

test("an unindexed delta falls back to the Flatpak-Ref header", () => {
  const id = deltaId("f".repeat(64));
  const headers = new Headers({ "Flatpak-Ref": "app/org.projectluma.Reel/x86_64/beta" });
  const event = countEvent({ pathname: `/repo/deltas/${id.slice(0, 2)}/${id.slice(2)}/superblock`, headers, maps, now: day });
  assert.equal(event.app_id, "org.projectluma.Reel");
  assert.equal(countEvent({ pathname: `/repo/deltas/${id.slice(0, 2)}/${id.slice(2)}/superblock`, headers: noHeaders, maps, now: day }), null);
});

test("events carry no client identifiers", () => {
  const id = deltaId(HEX_B);
  const headers = new Headers({ "CF-Connecting-IP": "203.0.113.9", "User-Agent": "libostree/2026.3 flatpak/1.18.2", "Flatpak-Ref": "app/org.projectluma.Canvas/x86_64/beta" });
  const event = countEvent({ pathname: `/repo/deltas/${id.slice(0, 2)}/${id.slice(2)}/superblock`, headers, maps, now: day });
  assert.deepEqual(Object.keys(event).sort(), ["app_id", "arch", "branch", "count", "day", "metric"]);
  assert.ok(!JSON.stringify(event).includes("203.0.113.9"));
  assert.ok(!JSON.stringify(event).includes("libostree"));
});

test("fetch handler proxies, sets cache policy and reports a counted pull", async () => {
  const calls = [];
  const waits = [];
  const id = deltaId(HEX_B);
  globalThis.caches = { default: { match: async () => undefined, put: async () => {} } };
  globalThis.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    calls.push({ url, init });
    if (url.endsWith("/apps/index.json")) return new Response(JSON.stringify(index), { status: 200 });
    if (url.startsWith("https://hub.example")) return new Response("{}", { status: 202 });
    return new Response("payload", { status: 200, headers: { "Content-Type": "application/octet-stream" } });
  };
  const env = { ORIGIN_URL: "https://origin.example", HUB_COUNTER_URL: "https://hub.example/api/depot/counters", DEPOT_COUNTER_SECRET: "s3cret" };
  const ctx = { waitUntil: (p) => waits.push(p) };

  const response = await worker.fetch(new Request(`https://dl.simplyluma.com/repo/deltas/${id.slice(0, 2)}/${id.slice(2)}/superblock`), env, ctx);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "public, max-age=31536000");
  await Promise.all(waits);
  const post = calls.find((c) => c.url.startsWith("https://hub.example"));
  assert.ok(post, "counter POSTed to Hub");
  assert.equal(post.init.headers.Authorization, "Bearer s3cret");
  assert.equal(JSON.parse(post.init.body).metric, "depot.install");

  const summary = await worker.fetch(new Request("https://dl.simplyluma.com/repo/summary"), env, ctx);
  assert.equal(summary.headers.get("Cache-Control"), "public, max-age=60");
  const refused = await worker.fetch(new Request("https://dl.simplyluma.com/repo/summary", { method: "PUT" }), env, ctx);
  assert.equal(refused.status, 405);
  const root = await worker.fetch(new Request("https://dl.simplyluma.com/"), env, ctx);
  assert.equal(root.headers.get("Content-Type"), "application/vnd.flatpak.repo");
});
