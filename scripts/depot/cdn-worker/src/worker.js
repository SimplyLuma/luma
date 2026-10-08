// SPDX-License-Identifier: Apache-2.0
//
// dl.simplyluma.com: serve the Luma remote from object storage and count
// installs and updates without tracking anyone (ADR-028 sections 9 and 12).
//
// Serving. Every request is proxied to the object-storage origin (a public,
// read-only bucket) and cached at the edge. Content-addressed files (OSTree
// objects and static deltas) never change and are cached for a year; the
// summary, its signature, descriptor files and the catalog change on every
// publication and are cached for a minute. Only GET and HEAD are accepted, so
// the Worker can never write to the bucket.
//
// Counting. The method is Flathub's: a pull of an app either downloads a
// static delta superblock (deltas/<from>-<to>/superblock is an update,
// deltas/<to>/superblock an install from scratch) or, without deltas, fetches
// the commit's root dirtree object exactly once. Flatpak names the ref it is
// pulling in the Flatpak-Ref header and the commit it upgrades from in
// Flatpak-Upgrade-From. apps/index.json, written at publication, maps each
// published commit and root dirtree to its ref, so objects are attributed
// even when a client sends no headers.
//
// What is sent to Hub is one aggregate increment:
//   {"metric": "depot.install" | "depot.update", "app_id", "arch", "branch",
//    "day": "YYYY-MM-DD", "count": 1}
// No IP address, user agent, country, header value or request identifier is
// read into the event or stored anywhere by this Worker.

const IMMUTABLE = /^\/repo\/(objects|deltas)\//;
const INDEX_PATH = "/apps/index.json";
const INDEX_TTL_SECONDS = 300;

export function deltaIdToCommit(id) {
  // OSTree's modified base64: '/' is written '_', padding is dropped.
  try {
    const binary = atob(id.replace(/_/g, "/") + "=");
    if (binary.length !== 32) return null;
    let hex = "";
    for (let i = 0; i < binary.length; i++) {
      hex += binary.charCodeAt(i).toString(16).padStart(2, "0");
    }
    return hex;
  } catch {
    return null;
  }
}

export function classify(pathname) {
  // -> {kind: "delta", from, to} | {kind: "dirtree", checksum} | null
  let m = pathname.match(/^\/repo\/deltas\/([^/]{2})\/([^/]+)\/superblock$/);
  if (m) {
    const id = m[1] + m[2];
    const dash = id.indexOf("-");
    if (dash === -1) return { kind: "delta", from: null, to: deltaIdToCommit(id) };
    return {
      kind: "delta",
      from: deltaIdToCommit(id.slice(0, dash)),
      to: deltaIdToCommit(id.slice(dash + 1)),
    };
  }
  m = pathname.match(/^\/repo\/objects\/([0-9a-f]{2})\/([0-9a-f]{62})\.dirtree$/);
  if (m) return { kind: "dirtree", checksum: m[1] + m[2] };
  return null;
}

export function parseRef(ref) {
  if (!ref) return null;
  const parts = ref.split("/");
  if (parts.length !== 4 || parts[0] !== "app") return null;
  const [, appId, arch, branch] = parts;
  // Flatpak application ids: three or more dot-separated elements
  if (!/^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z_][A-Za-z0-9_-]*){2,}$/.test(appId)) return null;
  if (!/^[a-z0-9_]+$/.test(arch)) return null;
  if (!/^[A-Za-z0-9_.-]+$/.test(branch)) return null;
  return { appId, arch, branch };
}

export function buildIndexMaps(index) {
  const byCommit = new Map();
  const byDirtree = new Map();
  for (const app of index.apps || []) {
    const ref = `app/${app.app_id}/${app.arch}/${app.branch}`;
    for (const commit of [app.commit, ...(app.previous_commits || [])]) {
      if (commit) byCommit.set(commit, ref);
    }
    for (const dirtree of [app.root_dirtree, ...(app.previous_root_dirtrees || [])]) {
      if (dirtree) byDirtree.set(dirtree, ref);
    }
  }
  return { byCommit, byDirtree };
}

// Decide whether a successful response is a countable app pull.
export function countEvent({ pathname, headers, maps, now = new Date() }) {
  const hit = classify(pathname);
  if (!hit) return null;
  let ref = null;
  let update = false;
  if (hit.kind === "delta") {
    if (!hit.to) return null;
    ref = maps.byCommit.get(hit.to) || null;
    update = hit.from !== null;
  } else {
    ref = maps.byDirtree.get(hit.checksum) || null;
    if (!ref) return null; // not a root dirtree of a published commit
    update = Boolean(headers.get("Flatpak-Upgrade-From"));
  }
  // The header names the ref when the object is not in the index yet (a
  // commit published minutes ago); the index wins when both are present.
  const parsed = parseRef(ref) || parseRef(headers.get("Flatpak-Ref"));
  if (!parsed) return null;
  return {
    metric: update ? "depot.update" : "depot.install",
    app_id: parsed.appId,
    arch: parsed.arch,
    branch: parsed.branch,
    day: now.toISOString().slice(0, 10),
    count: 1,
  };
}

function cacheSeconds(pathname) {
  return IMMUTABLE.test(pathname) ? 31536000 : 60;
}

async function loadIndex(env, ctx) {
  const cache = caches.default;
  const key = new Request(`https://index.invalid${INDEX_PATH}`);
  let response = await cache.match(key);
  if (!response) {
    const origin = await fetch(`${env.ORIGIN_URL}${INDEX_PATH}`, { cf: { cacheTtl: 0 } });
    if (!origin.ok) return { byCommit: new Map(), byDirtree: new Map() };
    response = new Response(await origin.text(), {
      headers: { "Cache-Control": `max-age=${INDEX_TTL_SECONDS}`, "Content-Type": "application/json" },
    });
    ctx.waitUntil(cache.put(key, response.clone()));
  }
  try {
    return buildIndexMaps(await response.json());
  } catch {
    return { byCommit: new Map(), byDirtree: new Map() };
  }
}

async function report(env, event) {
  if (!env.HUB_COUNTER_URL || !env.DEPOT_COUNTER_SECRET) return;
  await fetch(env.HUB_COUNTER_URL, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.DEPOT_COUNTER_SECRET}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(event),
  });
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    const url = new URL(request.url);
    let pathname = url.pathname;
    if (pathname === "/" || pathname === "") pathname = "/luma.flatpakrepo";
    if (pathname.includes("..")) return new Response("Bad Request", { status: 400 });

    const upstream = await fetch(`${env.ORIGIN_URL}${pathname}`, {
      method: request.method,
      headers: request.headers.get("Range") ? { Range: request.headers.get("Range") } : {},
      cf: { cacheEverything: true, cacheTtl: cacheSeconds(pathname) },
    });

    const headers = new Headers(upstream.headers);
    headers.set("Cache-Control", `public, max-age=${cacheSeconds(pathname)}`);
    headers.set("Access-Control-Allow-Origin", "*");
    headers.delete("x-amz-request-id");
    headers.delete("x-amz-meta-s3cmd-attrs");
    if (pathname.endsWith(".flatpakrepo")) headers.set("Content-Type", "application/vnd.flatpak.repo");
    if (pathname.endsWith(".flatpakref")) headers.set("Content-Type", "application/vnd.flatpak.ref");

    if (request.method === "GET" && upstream.status === 200 && classify(pathname)) {
      ctx.waitUntil((async () => {
        const maps = await loadIndex(env, ctx);
        const event = countEvent({ pathname, headers: request.headers, maps });
        if (event) await report(env, event);
      })().catch(() => {}));
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  },
};
