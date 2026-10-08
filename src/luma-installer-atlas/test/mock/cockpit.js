/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * TEST FIXTURE. A stand-in for the parts of cockpit.js Atlas uses, backed by
 * the in-memory Anaconda in anaconda.js. Bundled only into dist-preview/.
 *
 * URL parameters: surface=paper|ink|slate|contrast, platformLang=en-US,
 * battery=12 (percent, unplugged), offline=1, install=fail, speed=0.5,
 * inhibitor=0, eject=fail, channel=stable|beta|nightly, source=medium|online|test,
 * net=full|limited|portal|none
 */
import { createAnaconda } from "./anaconda.js";

const params = new URLSearchParams(typeof window !== "undefined" ? window.location.search : "");

const anaconda = createAnaconda({
    installOutcome: params.get("install") === "fail" ? "fail" : "succeed",
    installError: "Failed to pull payload: Writing object: No space left on device",
    resumeFailure: params.get("resumeFailure") === "1",
    installSpeed: Number(params.get("speed") || 1),
    latency: 5,
    offline: params.get("offline") === "1",
});

const files = {
    "/run/anaconda/anaconda.conf": "[Anaconda]\ndebug = False\n[Installation System]\ntype = BOOT_ISO\n[Installation Target]\nsystem_root = /mnt/sysroot\n[Storage]\ndefault_scheme = BTRFS\n[User Interface]\nhidden_webui_pages = \n",
    "/run/anaconda/backend_ready": "",
    "/run/anaconda/bus.address": "unix:path=/run/anaconda/mock",
    "/etc/os-release": "NAME=\"Fedora Linux\"\nPRETTY_NAME=\"Fedora Linux 44\"\n",
};

const delay = (ms, value) => new Promise(resolve => setTimeout(() => resolve(value), ms));

const cockpit = {
    format: (fmt, ...args) => String(fmt).replace(/\$([0-9]+)/g, (_, i) => args[Number(i)] ?? ""),
    format_bytes: (bytes) => `${Math.round(bytes / 1e9)} GB`,
    gettext: (context, text) => (text === undefined ? context : text),
    language: "en",
    language_direction: "ltr",
    location: { go: () => {}, path: [] },
    ngettext: (one, many, n) => (n === 1 ? one : many),
    noop: (text) => text,
    variant: (t, v) => ({ t, v }),
};

cockpit.file = (path, options = {}) => {
    const read = () => delay(path === "/run/anaconda/backend_ready" ? Number(params.get("backendDelay") || 2) : 2,
        path in files ? (options.syntax ? options.syntax.parse(files[path]) : files[path]) : null);
    return {
        close: () => {},
        modify: (fn) => delay(1, (files[path] = fn(files[path] || ""))),
        read,
        replace: (content) => {
            if (path === "/run/luma-atlas/viewer-heartbeat") {
                window.__atlasMockHeartbeats = (window.__atlasMockHeartbeats || 0) + 1;
            }
            return delay(1, content === null ? delete files[path] : (files[path] = content));
        },
        watch: (callback) => {
            read().then(content => callback(content));
            return { remove: () => {} };
        },
    };
};

const probeSystem = () => ({
    battery: params.get("battery") ? { acOnline: false, capacity: Number(params.get("battery")), charging: false, present: true } : null,
    bootOptions: { contrast: false, lang: null, surface: null },
    installerDisks: ["sdb"],
    media: { channel: params.get("channel") || "stable", enrolled: params.get("enrolled") === "1", ref: `luma/1/x86_64/${params.get("channel") || "stable"}`, source: params.get("source") || "medium" },
    network: { connectivity: params.get("net") || "full" },
    platformLang: params.has("platformLang") ? params.get("platformLang") : "en-US",
    stage2InMemory: true,
    uefi: true,
});

// The payload os-release PRETTY_NAME of each channel's release (release naming spec, 2026-09-17).
const MOCK_RELEASES = { beta: "Luma (Prairie, Beta 1)", nightly: "Luma (Prairie, Beta 0, Nightly 20260916)", stable: "Luma (Version 1, Prairie)" };

cockpit.spawn = (args) => {
    let closed = false;
    let rejectRun;
    const command = args.join(" ");
    const promise = new Promise((resolve, reject) => {
        rejectRun = reject;
        if (command.includes("atlas-probe system")) {
            resolve(JSON.stringify(probeSystem()));
        } else if (command.includes("atlas-probe network")) {
            resolve(JSON.stringify(probeSystem().network));
        } else if (command.includes("atlas-probe battery")) {
            resolve(JSON.stringify({ battery: probeSystem().battery }));
        } else if (command.includes("atlas-probe payload")) {
            resolve(JSON.stringify({ osName: "Luma", prettyName: params.get("source") === "online" ? null : MOCK_RELEASES[params.get("channel") || "stable"], ref: args[3], systemUsers: ["gdm", "polkitd", "root"], url: args[2], version: "1", zram: true }));
        } else if (command.includes("atlas-media record-location")) {
            resolve(JSON.stringify({ ok: true, timezone: args[2] }));
        } else if (command.includes("atlas-media eject")) {
            setTimeout(() => (params.get("eject") === "fail"
                ? reject(Object.assign(new Error(JSON.stringify({ ok: false, reason: "eject-failed" })), { message: JSON.stringify({ ok: false, reason: "eject-failed" }) }))
                : resolve(JSON.stringify({ disks: ["sdb"], ejected: ["sdb"], ok: true }))), Number(params.get("ejectDelay") || 1200));
        } else if (command.includes("atlas-media save-logs")) {
            resolve(JSON.stringify(params.get("logs") === "none"
                ? { ok: false, reason: "no-removable-target" }
                : { file: "luma-install-log-20260914-0941.tar.gz", label: "LOGS", ok: true }));
        } else if (args[0] === "systemd-inhibit" && args[1] === "--list") {
            resolve(params.get("inhibitor") === "0" ? "" : "Luma installer 0 root 1234 sleep sleep:idle:handle-lid-switch:handle-suspend-key Luma is being installed block");
        } else if (args[0] === "systemd-inhibit") {
            if (params.get("inhibitor") === "0") {
                reject(new Error("mock: no logind"));
            }
            // otherwise runs until closed
        } else if (args[0] === "/usr/bin/pwscore") {
            resolve("72\n");
        } else if (command.includes("encrypt-user-pw") || args.includes("-c")) {
            resolve("$y$j9T$mock");
        } else {
            resolve("");
        }
    });
    promise.close = () => {
        if (!closed) {
            closed = true;
            rejectRun?.(new Error("closed"));
        }
    };
    promise.input = () => promise;
    promise.stream = () => promise;
    promise.catch(() => {});
    return promise;
};

cockpit.dbus = () => ({
    addEventListener: () => {},
    call: (path, iface, method, args) => anaconda.call(path, iface, method, args),
    close: () => {},
    proxy: (iface, path) => anaconda.proxy(iface, path),
    subscribe: (match, callback) => anaconda.subscribe(match, callback),
});

export default cockpit;
