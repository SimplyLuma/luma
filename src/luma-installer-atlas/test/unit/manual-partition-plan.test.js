/* SPDX-License-Identifier: LGPL-2.1-or-later */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { build } from "esbuild";

// Render the real manual screen and invoke its actual Continue handler. Only
// the native D-Bus transport/task completion is substituted; the screen,
// applyPlan, applyStorage and ancestry/boot-drive selection remain product code.
const root = fileURLToPath(new URL("../../", import.meta.url));
const bundle = await build({
    absWorkingDir: root, bundle: true, write: false, platform: "node", format: "cjs",
    stdin: { resolveDir: root, loader: "jsx", contents: `
        import React from "react";
        import { renderToStaticMarkup } from "react-dom/server";
        import { MountPoints } from "./src/steps/MountPoints.jsx";
        import { StorageContext } from "./src/contexts/Common.jsx";
        export async function continueManual(devices, requests, keepBootOrder) {
            const continueRef = { current: null };
            const result = { next: false, saved: null };
            renderToStaticMarkup(React.createElement(StorageContext.Provider, {
                value: { diskSelection: { selectedDisks: ["nvme0n1", "nvme1n1"] },
                         deviceTrees: { "": { devices } } }
            }, React.createElement(MountPoints, {
                answers: { manualPath: "/manual", manualRequests: requests, manualKeepBootOrder: keepBootOrder },
                continueRef, index: 3, invalidateAfter: () => {}, back: () => {},
                next: () => { result.next = true; },
                setAnswers: update => { result.saved = update({}); }
            })));
            await continueRef.current();
            return result;
        }
    ` },
    plugins: [{ name: "native-storage-transport", setup(builder) {
        builder.onResolve({ filter: /^cockpit$/ }, () => ({ path: "cockpit", namespace: "fixture" }));
        builder.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ contents: `
            export default {
                gettext: value => value, ngettext: (one, many, n) => n === 1 ? one : many,
                variant: (t, v) => ({ t, v }), format: value => value,
                file: () => ({ modify: () => {} }), spawn: () => Promise.resolve(""),
                dbus: () => globalThis.__manualStorageTransport,
            };
        ` }));
        builder.onResolve({ filter: /(^|\/)storage\.js$/ }, args => {
            if (args.path.endsWith("/apis/storage.js") ||
                (args.path === "./storage.js" && args.importer.includes("/apis/")))
                return { path: "storage-client", namespace: "transport" };
        });
        builder.onLoad({ filter: /.*/, namespace: "transport" }, () => ({ contents: `
            export class StorageClient { constructor() { this.client = globalThis.__manualStorageTransport; } }
            export const runStorageTask = async ({ onSuccess }) => onSuccess();
        ` }));
    } }],
});
const compiled = { exports: {} };
new Function("require", "module", "exports", bundle.outputFiles[0].text)(
    createRequire(import.meta.url), compiled, compiled.exports);

const variant = v => ({ v });
const disk = () => ({ parents: variant([]), formatData: { type: variant("disklabel") } });
const partition = parent => ({ parents: variant([parent]), formatData: { type: variant("efi") } });
const requests = [
    { "device-spec": "nvme0n1p1", "mount-point": "/boot/efi", "format-type": "efi", reformat: false },
    { "device-spec": "root", "mount-point": "/", "format-type": "btrfs", reformat: true },
];
for (const separateDisk of [false, true]) {
    for (const keepBootOrder of [false, true]) {
        test(`manual Continue reaches validation: separate EFI disk=${separateDisk}, preserve startup=${keepBootOrder}`, async () => {
            const calls = [];
            globalThis.__manualStorageTransport = {
                async call(path, iface, method, args) {
                    calls.push({ path, iface, method, args });
                    if (method === "Get") return [variant("MANUAL")];
                    if (method === "GatherRequests") return [requests.map(row =>
                        Object.fromEntries(Object.entries(row).map(([key, value]) => [key, variant(value)])))];
                    if (method === "ConfigureWithTask") return ["/configure-task"];
                    if (method === "ValidateWithTask") return ["/validate-task"];
                    return [];
                },
                proxy: () => ({ GetResult: async () => variant({ "error-messages": variant([]), "warning-messages": variant([]) }) }),
            };
            const devices = {
                nvme0n1: disk(), nvme1n1: disk(), nvme0n1p1: partition("nvme0n1"),
                root: partition(separateDisk ? "nvme1n1" : "nvme0n1"),
            };
            const result = await compiled.exports.continueManual(devices, requests, keepBootOrder);
            assert.equal(result.next, true, "The actual manual Continue handler must reach review");
            assert.equal(result.saved.manualKeepBootOrder, keepBootOrder);
            const writes = calls.filter(call => call.method === "Set");
            assert.equal(writes.find(call => call.args[1] === "Drive").args[2].v, separateDisk ? "nvme0n1" : "");
            assert.equal(writes.find(call => call.args[1] === "KeepBootOrder").args[2].v, keepBootOrder);
            assert.ok(calls.some(call => call.method === "ApplyPartitioning"));
            assert.ok(calls.some(call => call.method === "ValidateWithTask"));
            assert.deepEqual(requests[0], { "device-spec": "nvme0n1p1", "mount-point": "/boot/efi", "format-type": "efi", reformat: false });
        });
    }
}
