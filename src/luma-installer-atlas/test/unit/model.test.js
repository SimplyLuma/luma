/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Unit tests for Atlas's pure model modules. Run: node --test test/unit
 * The device data below mirrors the shape Anaconda's DeviceTree.Viewer
 * returns (a{sv} values unwrapped by cockpit to {t, v}).
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import catalog from "../../src/data/app-collections.json" with { type: "json" };
import { cleanSelection, collectionRows, firstBootApps, reviewAppsText, selectionNote, toggleCollection } from "../../src/model/apps.js";
import { fullNameProblem, guessUsername, secretProblems, usernameProblem } from "../../src/model/account.js";
import { clockReading } from "../../src/model/clock.js";
import { driveListProblem, driveRows, driveSentence, driveTitle, mergeDriveFacts } from "../../src/model/drives.js";
import { displayCity, displayRegion, formatSize, joinList } from "../../src/model/format.js";
import { detectedLocaleFromPlatformLang, parsePlatformLang, pickLanguageRows } from "../../src/model/languages.js";
import { durationClause, durationSentence, finishedLede, installingText, networkAdvice, releaseName } from "../../src/model/delivery.js";
import { layoutLine } from "../../src/model/layout.js";
import { batteryAdvice } from "../../src/model/power.js";
import {
    applyCategory, applyProgress, applySteps, applySucceeded, initialProgress, percent, PHASES, phaseStates, remember,
} from "../../src/model/progress.js";
import { primaryLabel, reviewWarning } from "../../src/model/review.js";

const V = (v, t = "s") => ({ t, v });
const GiB = 1024 ** 3;
const MiB = 1024 ** 2;

const device = ({ attrs = {}, children = [], description = "", format = "", formatAttrs = {}, id, isDisk = false, name, parents = [], removable = false, size, type, free, total }) => ({
    attrs: V(attrs, "a{ss}"),
    children: V(children, "as"),
    description: V(description),
    "device-id": V(id),
    formatData: { attrs: V(formatAttrs, "a{ss}"), mountable: V(["ext4", "btrfs", "xfs", "vfat", "efi"].includes(format), "b"), type: V(format) },
    free: free === undefined ? undefined : V(String(free)),
    "is-disk": V(isDisk, "b"),
    name: V(name || id),
    parents: V(parents, "as"),
    protected: V(false, "b"),
    removable: V(removable, "b"),
    size: V(size, "t"),
    total: total === undefined ? undefined : V(String(total)),
    type: V(type),
});

const originalDevices = () => ({
    nvme0n1: device({ attrs: { bus: "nvme", model: "Samsung SSD 990 PRO 1TB", vendor: "" }, description: "Samsung SSD 990 PRO 1TB", format: "", free: 1000204886016, id: "nvme0n1", isDisk: true, size: 1000204886016, total: 1000204886016, type: "disk" }),
    sda: device({ attrs: { bus: "ata", model: "CT500MX500SSD1", vendor: "ATA" }, children: ["sda1", "sda2", "sda3"], format: "disklabel", free: 190 * 1000 ** 3, id: "sda", isDisk: true, size: 500107862016, total: 500107862016, type: "disk" }),
    sda1: device({ format: "efi", id: "sda1", parents: ["sda"], size: 100 * MiB, type: "partition" }),
    sda2: device({ format: "ntfs", id: "sda2", parents: ["sda"], size: 400 * 1000 ** 3, type: "partition" }),
    sda3: device({ format: "ntfs", id: "sda3", parents: ["sda"], size: 600 * MiB, type: "partition" }),
    sdb: device({ attrs: { bus: "usb", model: "Ultra", vendor: "SanDisk" }, format: "iso9660", id: "sdb", isDisk: true, removable: true, size: 61530439680, total: 61530439680, type: "disk" }),
});

const windows = [{ devices: V(["sda2", "sda1"], "as"), "mount-points": V({}, "a{ss}"), "os-name": V("Windows 11") }];

test("formatSize reads like a drive label", () => {
    assert.equal(formatSize(1000204886016), "1 TB");
    assert.equal(formatSize(500107862016), "500 GB");
    assert.equal(formatSize(600 * MiB), "629 MB");
    assert.equal(formatSize(1.5 * 1000 ** 3), "1.5 GB");
    assert.equal(formatSize(999.7 * 1000 ** 3), "1 TB");
    assert.equal(formatSize(0), "0 B");
});

test("joinList and tz display names", () => {
    assert.equal(joinList(["a"]), "a");
    assert.equal(joinList(["a", "b"]), "a and b");
    assert.equal(joinList(["a", "b", "c"]), "a, b and c");
    assert.equal(displayCity("Los_Angeles"), "Los Angeles");
    assert.equal(displayCity("Argentina/Buenos_Aires"), "Buenos Aires, Argentina");
    assert.equal(displayRegion("America"), "Americas");
    assert.equal(displayRegion("Europe"), "Europe");
});

const languages = {
    de: { locales: [{ "english-name": V("German (Germany)"), "language-id": V("de"), "locale-id": V("de_DE.UTF-8"), "native-name": V("Deutsch (Deutschland)") }] },
    en: {
        locales: [
            { "english-name": V("English (United States)"), "language-id": V("en"), "locale-id": V("en_US.UTF-8"), "native-name": V("English (United States)") },
            { "english-name": V("English (United Kingdom)"), "language-id": V("en"), "locale-id": V("en_GB.UTF-8"), "native-name": V("English (United Kingdom)") },
        ],
    },
    es: { locales: [{ "english-name": V("Spanish (Spain)"), "language-id": V("es"), "locale-id": V("es_ES.UTF-8"), "native-name": V("Español (España)") }] },
    fr: { locales: [{ "english-name": V("French (France)"), "language-id": V("fr"), "locale-id": V("fr_FR.UTF-8"), "native-name": V("Français (France)") }] },
    ja: { locales: [{ "english-name": V("Japanese (Japan)"), "language-id": V("ja"), "locale-id": V("ja_JP.UTF-8"), "native-name": V("日本語 (日本)") }] },
};

test("Detected comes only from a real firmware value", () => {
    assert.deepEqual(parsePlatformLang("en-US\0"), { language: "en", territory: "US" });
    assert.equal(detectedLocaleFromPlatformLang(languages, "en-GB"), "en_GB.UTF-8");
    assert.equal(detectedLocaleFromPlatformLang(languages, "en"), null, "bare language with several territories claims nothing");
    assert.equal(detectedLocaleFromPlatformLang(languages, "fr"), "fr_FR.UTF-8");
    assert.equal(detectedLocaleFromPlatformLang(languages, null), null);

    const common = ["de_DE.UTF-8", "en_US.UTF-8", "es_ES.UTF-8", "fr_FR.UTF-8", "ja_JP.UTF-8"];
    const undetected = pickLanguageRows({ commonLocales: common, current: "en_US.UTF-8", detected: null, languages });
    assert.equal(undetected.length, 5);
    assert.ok(undetected.every(row => row.tag === null), "the default language is never tagged Detected");
    assert.deepEqual(undetected.map(row => row.name), ["English", "Español", "Français", "Deutsch", "日本語"]);
    assert.equal(undetected[1].region, "España");

    const detected = pickLanguageRows({ commonLocales: common, current: "en_US.UTF-8", detected: "en_US.UTF-8", languages });
    assert.equal(detected[0].tag, "Detected");
});

test("drive rows name what is on each drive and keep the installer out", () => {
    const devices = originalDevices();
    const rows = driveRows({ devices, existingSystems: windows, installerDisks: ["sdb"], requiredSize: 25 * 1000 ** 3, usableDisks: ["nvme0n1", "sda"] });
    assert.equal(rows.length, 3);
    assert.equal(rows[0].title, "Internal drive · 1 TB");
    assert.equal(rows[0].sentence, "Empty · Samsung SSD 990 PRO 1TB");
    assert.equal(rows[1].sentence, "Windows 11 is on this drive · 310 GB in partitions");
    assert.equal(rows[2].isInstaller, true);
    assert.equal(rows[2].title, "USB drive · 62 GB");
    assert.equal(rows[2].sentence, "SanDisk Ultra · this is your installer");
    assert.equal(driveTitle(devices.sdb), "USB drive · 62 GB");

    assert.equal(driveListProblem({ requiredSize: 25e9, rows: rows.filter(row => row.isInstaller) }).kind, "only-installer");
    const small = driveRows({ devices: { vda: device({ id: "vda", isDisk: true, size: 16e9, total: 16e9, free: 16e9, type: "disk" }) }, existingSystems: [], requiredSize: 25e9, usableDisks: ["vda"] });
    assert.equal(driveListProblem({ requiredSize: 25e9, rows: small }).text, "Luma needs about 25 GB; the largest drive here is 16 GB.");
});

test("drive facts survive a plan reset that hides unselected drives", () => {
    const full = originalDevices();
    const first = mergeDriveFacts(null, { devices: full, disks: ["nvme0n1", "sda", "sdb"], existingSystems: windows });
    const afterReset = { nvme0n1: { ...full.nvme0n1, children: V(["nvme0n1p1"], "as") }, nvme0n1p1: full.sda1, sdb: full.sdb, sda: { ...full.sda, children: V([], "as") } };
    const merged = mergeDriveFacts(first, { devices: afterReset, disks: ["nvme0n1", "sda", "sdb"], existingSystems: [] });
    assert.ok(merged.devices.sda2, "the Windows partition is still known");
    assert.equal(merged.existingSystems.length, 1);
    assert.equal(merged.devices.nvme0n1.children.v.length, 0, "a later plan does not replace the drive as first seen");
    assert.equal(driveSentence({ devices: merged.devices, disk: "sda", existingSystems: merged.existingSystems, isInstaller: false }),
        "Windows 11 is on this drive · 310 GB in partitions");
});

test("BitLocker is named on the drive", () => {
    const devices = originalDevices();
    devices.sda2.formatData.type = V("bitlocker");
    assert.equal(driveSentence({ devices, disk: "sda", existingSystems: windows, isInstaller: false }),
        "Windows 11 is on this drive, locked with BitLocker · 310 GB in partitions");
});

const plannedBtrfs = () => ({
    "btrfs-vol": device({ format: "btrfs", id: "btrfs-vol", name: "luma", parents: ["luks-1"], size: 998 * 1000 ** 3, type: "btrfs volume" }),
    home: device({ format: "btrfs", id: "home", name: "home", parents: ["btrfs-vol"], size: 998 * 1000 ** 3, type: "btrfs subvolume" }),
    "luks-1": device({ format: "btrfs", id: "luks-1", parents: ["nvme0n1p3"], size: 998 * 1000 ** 3, type: "luks/dm-crypt" }),
    nvme0n1: device({ children: ["nvme0n1p1", "nvme0n1p2", "nvme0n1p3"], format: "disklabel", id: "nvme0n1", isDisk: true, size: 1000204886016, type: "disk" }),
    nvme0n1p1: device({ format: "efi", id: "nvme0n1p1", parents: ["nvme0n1"], size: 600 * 1000 ** 2, type: "partition" }),
    nvme0n1p2: device({ format: "ext4", id: "nvme0n1p2", parents: ["nvme0n1"], size: 1000 ** 3, type: "partition" }),
    nvme0n1p3: device({ format: "luks", id: "nvme0n1p3", parents: ["nvme0n1"], size: 998 * 1000 ** 3, type: "partition" }),
    root: device({ format: "btrfs", id: "root", name: "root", parents: ["btrfs-vol"], size: 998 * 1000 ** 3, type: "btrfs subvolume" }),
    sda9: device({ format: "swap", id: "sda9", parents: ["sda"], size: 8 * 1000 ** 3, type: "partition" }),
    sda: device({ children: ["sda9"], id: "sda", isDisk: true, size: 500e9, type: "disk" }),
});

test("layout line follows the real plan: subvolumes, no invented swap", () => {
    const mountPoints = { "/": "root", "/boot": "nvme0n1p2", "/boot/efi": "nvme0n1p1", "/home": "home" };
    const devices = plannedBtrfs();
    assert.equal(layoutLine({ devices, mountPoints, selectedDisks: ["nvme0n1"], zram: true }),
        "/ and /home on btrfs 929 GiB (subvolumes root and home) · /boot 954 MiB · /boot/efi 572 MiB · swap in memory (zram)");
    assert.equal(layoutLine({ devices, mountPoints, selectedDisks: ["nvme0n1"], zram: false }),
        "/ and /home on btrfs 929 GiB (subvolumes root and home) · /boot 954 MiB · /boot/efi 572 MiB",
        "no swap and no zram claim when the payload has no zram config; the other drive's swap is not ours");
    const withSwap = { ...devices, nvme0n1p4: device({ format: "swap", id: "nvme0n1p4", parents: ["nvme0n1"], size: 8e9, type: "partition" }) };
    assert.match(layoutLine({ devices: withSwap, mountPoints, selectedDisks: ["nvme0n1"], zram: null }), /swap 7.5 GiB$/);
});

test("review warning names the erased drive and every untouched drive", () => {
    const devices = originalDevices();
    const warning = reviewWarning({ devices, existingSystems: windows, installerDisks: ["sdb"], scenarioId: "erase-all", selectedDisks: ["nvme0n1"], usableDisks: ["nvme0n1", "sda"] });
    assert.equal(warning.strong, "Everything on the 1 TB drive will be erased.");
    assert.equal(warning.plain, "The 500 GB drive with Windows 11 on it and your installer USB drive will not be touched.");

    const onWindows = reviewWarning({ devices, existingSystems: windows, installerDisks: ["sdb"], scenarioId: "erase-all", selectedDisks: ["sda"], usableDisks: ["nvme0n1", "sda"] });
    assert.equal(onWindows.strong, "Everything on the 500 GB drive, including Windows 11, will be erased.");

    const share = reviewWarning({ devices, existingSystems: windows, installerDisks: ["sdb"], scenarioId: "use-free-space", selectedDisks: ["sda"], usableDisks: ["nvme0n1", "sda"] });
    assert.equal(share.strong, "Luma will use the free space on the 500 GB drive.");
    assert.equal(share.plain, "Nothing already on it will be erased. The 1 TB drive and your installer USB drive will not be touched.");

    assert.equal(primaryLabel({ scenarioId: "erase-all" }), "Erase this drive and install");
    assert.equal(primaryLabel({ scenarioId: "use-free-space" }), "Install Luma");
});

test("username, name and secret rules speak under the field", () => {
    assert.equal(usernameProblem(""), null);
    assert.equal(usernameProblem("sam"), null);
    assert.equal(usernameProblem("Sam"), "Use lowercase letters only.");
    assert.equal(usernameProblem("sam r"), "Usernames cannot have spaces.");
    assert.equal(usernameProblem("root"), "“root” is reserved for the system. Pick another name.");
    assert.equal(usernameProblem("gdm", { taken: ["gdm"] }), "“gdm” is already used by the system. Pick another name.");
    assert.equal(usernameProblem("9lives"), "Start with a lowercase letter.");
    assert.equal(usernameProblem("a".repeat(33)), "Keep it to 32 characters or fewer.");
    assert.equal(fullNameProblem("a:b"), "Names cannot contain a colon.");
    assert.equal(guessUsername("Zoë Martín"), "zoe");

    const policy = { "is-strict": V(false, "b"), "min-length": V(8, "i"), "min-quality": V(1, "i") };
    assert.equal(secretProblems({ confirm: "", noun: "passphrase", policy, secret: "short" }).secretProblem, "Use at least 8 characters.");
    const mismatch = secretProblems({ confirm: "correct horse", noun: "passphrase", policy, secret: "correct horse battery" });
    assert.equal(mismatch.confirmProblem, "These do not match.");
    assert.equal(mismatch.valid, false);
    assert.equal(secretProblems({ confirm: "correct horse battery", noun: "passphrase", policy, secret: "correct horse battery" }).valid, true);
    assert.deepEqual(secretProblems({ confirm: "crème brûlée!", noun: "passphrase", policy, secret: "crème brûlée!" }).notes,
        ["Some of these characters may be hard to type when the computer starts."]);
});

test("progress phases light in Anaconda's real order and never regress", () => {
    assert.equal(new Set(PHASES).size, PHASES.length, "each stage has a distinct label");
    assert.equal(PHASES.filter(label => label === "Finishing up").length, 1);
    assert.equal(PHASES.at(-1), "Finishing up", "finishing is the final stage only");
    let state = applySteps(initialProgress(), 40);
    state = applyCategory(state, "ENVIRONMENT_CONFIGURATION");
    assert.deepEqual(phaseStates(state), ["doing", "todo", "todo", "todo"]);
    state = applyCategory(state, "STORAGE_CONFIGURATION");
    state = applyProgress(state, 4, "Creating btrfs on /dev/mapper/luks");
    state = applyCategory(state, "SOFTWARE_INSTALLATION");
    assert.equal(state.phase, 1);
    state = applyProgress(state, 8, "Receiving objects: 50% (1000/2000) 400 MB");
    assert.equal(percent(state), 47, "the payload fills most of the meter from its own percentage");
    state = remember(state);
    state = applyCategory(state, "STORAGE_CONFIGURATION");
    assert.equal(state.phase, 1, "late storage does not move the meter back");
    assert.ok(percent(state) >= 47, "the percentage never goes down");
    state = applyCategory(state, "BOOTLOADER_INSTALLATION");
    assert.equal(state.phase, 2);
    state = applyCategory(state, "SYSTEM_CONFIGURATION");
    assert.equal(state.phase, 2);
    state = applyCategory(state, "BOOTLOADER_INSTALLATION");
    assert.equal(state.phase, 3, "initramfs after system configuration is finishing off");
    state = applyProgress(state, 40, "");
    assert.equal(percent(state), 99, "never 100 before Succeeded");
    state = applySucceeded(state);
    assert.equal(percent(state), 100);
    assert.deepEqual(phaseStates(state), ["done", "done", "done", "done"]);
});

test("clock line is computed from the zone", () => {
    const now = new Date(Date.UTC(2026, 7, 18, 16, 41));
    assert.deepEqual(clockReading({ locale: "en-US", now, zone: "America/Los_Angeles" }), { date: "Tuesday, August 18", time: "9:41 AM" });
    assert.deepEqual(clockReading({ locale: "en-US", now, zone: "Europe/Berlin" }), { date: "Tuesday, August 18", time: "6:41 PM" });
});

test("low battery blocks only when unplugged and low", () => {
    assert.equal(batteryAdvice(null), null);
    assert.equal(batteryAdvice({ acOnline: true, capacity: 5, charging: false, present: true }), null);
    assert.equal(batteryAdvice({ acOnline: false, capacity: 80, charging: false, present: true }), null);
    assert.equal(batteryAdvice({ acOnline: false, capacity: 12, charging: false, present: true }).strong, "Plug in first.");
});

test("app collections: nothing preselected, only known ids recorded", () => {
    const rows = collectionRows(catalog);
    assert.deepEqual(rows.map(row => row.id), ["creative", "studio"]);
    assert.equal(rows[0].subtitle, "Canvas, Reel and Darkroom · Design, video and photos");
    let selected = [];
    assert.equal(reviewAppsText(catalog, selected), "None");
    assert.equal(selectionNote(catalog, selected), null);
    selected = toggleCollection(selected, "studio");
    selected = toggleCollection(selected, "creative");
    assert.deepEqual(cleanSelection(catalog, [...selected, "nonsense", "office"]), ["creative", "studio"]);
    assert.deepEqual(firstBootApps(catalog, [...selected, "office"]), { applications: [], collections: ["creative", "studio"] });
    assert.equal(reviewAppsText(catalog, selected), "Creative and Studio · downloads when Luma first starts");
    assert.match(selectionNote(catalog, selected), /^Canvas, Reel, Darkroom and Session will download the first time Luma starts/);
    assert.deepEqual(toggleCollection(selected, "creative"), ["studio"]);
    assert.equal(reviewAppsText(catalog, ["office"]), "None", "stale Office choices are not promised");
});

test("delivery: source, network, duration and concise completion", () => {
    const payload = { prettyName: "Luma (Version 1, Prairie)" };
    assert.equal(installingText({ media: { channel: "stable", source: "medium" }, payload }), "Luma (Version 1, Prairie) · Stable · from this USB drive");
    const nightlyPayload = { osName: "Luma", prettyName: "Luma (Prairie, Beta 0, Nightly 20260916)" };
    assert.equal(installingText({ media: { channel: "nightly", source: "medium" }, payload: nightlyPayload }), "Luma (Prairie, Beta 0, Nightly 20260916) · from this USB drive");
    assert.equal(installingText({ media: { channel: "beta", source: "medium" }, payload: { prettyName: "Luma (Prairie, Beta 1.1)" } }), "Luma (Prairie, Beta 1.1) · from this USB drive");
    assert.equal(installingText({ media: { channel: "nightly", source: "medium" }, payload: { prettyName: "Fedora Linux 44 (Silverblue)" } }), "Luma · Nightly · from this USB drive");
    assert.equal(releaseName(nightlyPayload), "Luma (Prairie, Beta 0, Nightly 20260916)");
    assert.equal(releaseName({ prettyName: "Lumalike 3" }), null);
    assert.equal(releaseName(undefined), null);
    assert.equal(finishedLede(nightlyPayload), "Take the USB drive out, then restart.");
    assert.equal(finishedLede({}), "Take the USB drive out, then restart.");
    assert.equal(installingText({ media: { channel: "stable", source: "online" }, payload: {} }), "Luma · Stable · downloaded as it installs");
    assert.equal(installingText({ media: { channel: "nightly", ref: "luma/1/x86_64/nightly", source: "test" }, payload: { ref: "fedora/44/x86_64/silverblue" } }),
                 "fedora/44/x86_64/silverblue (a test system) · updates from Nightly");
    assert.equal(installingText({ media: null, payload: { ref: "x/y" } }), "x/y");

    const online = { channel: "stable", source: "online" };
    assert.equal(networkAdvice({ media: { channel: "stable", source: "medium" }, network: { connectivity: "none" } }), null);
    assert.equal(networkAdvice({ media: online, network: { connectivity: "full" } }), null);
    assert.equal(networkAdvice({ media: online, network: { connectivity: "unknown" } }), null);
    assert.equal(networkAdvice({ media: online, network: { connectivity: "none" } }).strong, "Connect to the internet first.");
    assert.match(networkAdvice({ media: online, network: { connectivity: "portal" } }).plain, /sign in/);

    assert.equal(durationSentence({ source: "medium" }), "This usually takes under ten minutes.");
    assert.equal(durationSentence(online), "How long this takes depends on your internet connection.");
    assert.equal(batteryAdvice({ acOnline: false, capacity: 12, charging: false, present: true }, durationClause(online)).plain,
                 "The battery is at 12%, and downloading Luma can take a while.");

    assert.doesNotMatch(networkAdvice({ media: online, network: { connectivity: "none" } }).plain, /—|"|'/);

});


test("offline OSTree activity without a percentage is indeterminate", () => {
    let state = applyCategory(applySteps(initialProgress(), 49), "SOFTWARE_INSTALLATION");
    assert.equal(percent(state), null);
    state = remember(applyProgress(state, 8, "Writing objects"));
    assert.equal(percent(state), null, "old source falsely displayed fixed 5% here");
    assert.equal(state.best, 0);
    state = remember(applyProgress(state, 8, "Receiving objects: 25% (50/200) 1 MB"));
    assert.equal(percent(state), 26);
    state = remember(applyProgress(state, 8, "Writing objects"));
    assert.equal(percent(state), null, "numeric fetch progress does not invent write progress");
    assert.equal(state.best, 26, "last real measurement stays available to later phases");
    assert.equal(percent(applySucceeded(state)), 100);
});
