#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Combine the shipped Titan shell source with the merged Mobile experience.

Requires the exact source RPM as an operator-supplied input. No packages,
private device evidence, signing material or credentials are bundled here.
"""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

SOURCE_SHA256 = "94368f1211beaf59c981bf9f21d94d1cf96a292eee39a0a7edccf8d931e80a84"
SOURCE_NAME = "phosh-0254669ff329754471a468d2674341d4871a92b3"


def prepare(srpm, output, repository):
    with srpm.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != SOURCE_SHA256:
            raise ValueError("not the supported Titan Phosh 83.1 source RPM")
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "srpm"
    inputs.mkdir()
    producer = subprocess.Popen(["rpm2cpio", str(srpm)], stdout=subprocess.PIPE)
    try:
        subprocess.run(["cpio", "-idm", "--quiet"], stdin=producer.stdout, cwd=inputs, check=True)
    finally:
        producer.stdout.close()
    if producer.wait() != 0:
        raise RuntimeError("source RPM extraction failed")
    sources = output / "full-source"
    sources.mkdir()
    for name in (SOURCE_NAME, "libgnome-volume-control-d2442f455844e5292cb4a74ffc66ecc8d7595a9f", "libcall-ui-v0.1.5"):
        with tarfile.open(inputs / (name + ".tar.gz")) as archive:
            archive.extractall(sources, filter="data")
    source = sources / SOURCE_NAME
    patches = sorted((int(n), name) for n, name in re.findall(
        r"^Patch(\d+):\s+(\S+)", (inputs / "luma-phosh.spec").read_text(), re.M))
    with (output / "assembly.log").open("w") as log:
        for _, name in patches:
            subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(inputs / name)],
                           cwd=source, stdout=log, stderr=subprocess.STDOUT, check=True)
        # The pinned phone cohort has output-fit, call/audio, cached-shadow and
        # gesture changes after the common baseline. Preserve all of those.
        expected = {44: 0, 45: 0, 46: 0, 47: 1, 48: 0, 49: 1, 50: 1, 51: 1}
        for number, status in expected.items():
            patch = next((repository / "patches/phosh").glob(f"{number:04d}-*.patch"))
            result = subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(patch)],
                                    cwd=source, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode != status:
                raise RuntimeError(f"unexpected source compatibility result: {patch.name}")
    allowed = {"src/app-grid.c.rej", "src/meson.build.rej", "src/luma-today.c.rej", "src/stylesheet/common.css.rej"}
    rejects = list(source.rglob("*.rej"))
    if {str(p.relative_to(source)) for p in rejects} != allowed:
        raise RuntimeError("unexpected compatibility conflicts")
    path = source / "src/app-grid.c"
    text = path.read_text().replace('#include "luma-today.h"', '#include "luma-today.h"\n#include "luma-home-pages.h"', 1)
    text = text.replace("  GtkWidget *today_slot;", "  GtkWidget *today_slot;\n  GtkWidget *home_pages;", 1)
    text = text.replace("app_ids[i] && g_list_model_get_n_items (G_LIST_MODEL (priv->home_model)) < priv->home_slots", "app_ids[i]")
    text = text.replace('  g_signal_connect (priv->favs, "size-allocate", G_CALLBACK (on_home_grid_allocated), self);\n', "")
    path.write_text(text)
    path = source / "src/meson.build"
    path.write_text(path.read_text().replace("  'luma-today.c',", "  'luma-today.c',\n  'luma-home-pages.c',", 1))
    path = source / "src/stylesheet/common.css"
    text = path.read_text().replace("box-shadow: 0 18px 40px -16px rgba(0,0,0,.6);", "box-shadow: none;")
    additions = "\n".join(line[1:] for line in path.with_suffix(".css.rej").read_text().splitlines()
                          if line.startswith("+") and not line.startswith("+++"))
    path.write_text(text + "\n" + additions + "\n.luma-today-card { min-height: 114px; }\n")
    patch = (repository / "patches/phosh/0051-luma-shared-home-widgets.patch").read_text().split("--- a/src/meson.build")[0]
    body = patch[patch.index("@@"):].splitlines()[1:]
    replacement = "\n".join(line[1:] for line in body if line.startswith(("+", " "))) + "\n"
    if not replacement.rstrip().endswith("{"):
        raise RuntimeError("shared widget constructor boundary changed")
    compact = (Path(__file__).with_name("titan2e-experience") / "compact-today.c.inc").read_text()
    (source / "src/luma-today.c").write_text(replacement + "  return g_object_new (PHOSH_TYPE_LUMA_TODAY, NULL);\n}\n" + compact)
    for path in rejects:
        path.unlink()
    for target, name in (("gvc", "libgnome-volume-control-d2442f455844e5292cb4a74ffc66ecc8d7595a9f"), ("libcall-ui", "libcall-ui-v0.1.5")):
        destination = source / "subprojects" / target
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(sources / name), destination)
    shutil.copytree(repository / "src/luma-home-widgets", output / "home-widgets")
    print(source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--srpm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.srpm.resolve(strict=True), args.output.resolve(), Path(__file__).resolve().parents[2])
