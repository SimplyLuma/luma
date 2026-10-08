#!/usr/bin/env node
/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Build Atlas with esbuild, as anaconda-webui 68 does, without PatternFly or
 * Sass. Run ./fetch-build-inputs.sh first.
 *
 *   node build.js                 product bundle   -> dist/
 *   ATLAS_PREVIEW=1 node build.js mocked backend   -> dist-preview/  (never packaged)
 */
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { startupLockupSvg } from "./src/model/startup.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const preview = process.env.ATLAS_PREVIEW === "1";
const production = process.env.NODE_ENV === "production" || !preview;
const outdir = path.join(here, preview ? "dist-preview" : "dist");
const inputs = path.join(here, ".build-inputs");
const cockpitLib = path.join(inputs, "pkg", "lib");

if (!fs.existsSync(path.join(cockpitLib, "cockpit.js"))) {
    console.error("error: missing build inputs; run ./fetch-build-inputs.sh");
    process.exit(1);
}

fs.rmSync(outdir, { force: true, recursive: true });
fs.mkdirSync(path.join(outdir, "fonts"), { recursive: true });
const wordmark = fs.existsSync(path.join(here, "brand/luma-wordmark.svg"))
    ? path.join(here, "brand/luma-wordmark.svg")
    : path.join(here, "../../website/public/brand/luma-wordmark.svg");
fs.writeFileSync(path.join(outdir, "loader-background.svg"), startupLockupSvg(fs.readFileSync(wordmark, "utf8")));

const mockPlugin = {
    name: "atlas-mock-backend",
    setup (build) {
        build.onResolve({ filter: /^cockpit$/ }, () => ({ path: path.join(here, "test", "mock", "cockpit.js") }));
    },
};

await esbuild.build({
    bundle: true,
    entryPoints: [path.join(here, "src", "index.js")],
    external: ["*.ttf"],
    legalComments: "external",
    loader: { ".js": "jsx", ".py": "text" },
    minify: production,
    nodePaths: [cockpitLib],
    outdir,
    plugins: preview ? [mockPlugin] : [],
    sourcemap: "linked",
    target: ["es2020", "safari15"],
    define: { "process.env.NODE_ENV": JSON.stringify(production ? "production" : "development") },
});

for (const [from, to] of [
    [path.join(here, "src", "index.html"), "index.html"],
    [path.join(here, "src", "manifest.json"), "manifest.json"],
    [path.join(inputs, "fonts", "Figtree.ttf"), path.join("fonts", "Figtree.ttf")],
    [path.join(inputs, "fonts", "OFL.txt"), path.join("fonts", "OFL.txt")],
]) {
    fs.copyFileSync(from, path.join(outdir, to));
}

console.log(`atlas: built ${path.relative(here, outdir)}`);
