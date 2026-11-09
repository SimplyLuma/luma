# SPDX-License-Identifier: Apache-2.0
"""Turn package facts and a listing into the checks a developer sees.

Every check has a stable key, ok true/false, and one plain sentence. A failed
check sends the submission back as Needs changes with that sentence; review
(Nick, in admin) sees the detail too.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import words

CONFIG = Path(__file__).resolve().parent.parent / "config"
APP_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*){2,}\Z")
SUMMARY_MAX = 60
DESKTOP_MIN = (1180, 698)
PHONE_SIZE = (1170, 2532)
ICON_MIN = 512


def load_policy() -> dict:
    return json.loads((CONFIG / "policy.json").read_text())


def check(key: str, ok: bool, message: str, detail: str = "", **extra) -> dict:
    item = {"key": key, "ok": bool(ok), "message": message}
    if detail:
        item["detail"] = detail
    item.update(extra)
    return item


def reserved(app_id: str, policy: dict, luma_ids: set[str]) -> str | None:
    lowered = app_id.lower()
    for prefix in policy["reserved_prefixes"]:
        if lowered.startswith(prefix.lower()):
            return f"App ids starting with {prefix} belong to Luma. Use a reverse-DNS id for a domain you own."
    if app_id in luma_ids:
        return "This app id is already used by one of Luma's own apps. Use a reverse-DNS id for a domain you own."
    return None


def runtime_allowed(runtime: str, policy: dict) -> bool:
    name, _, rest = runtime.partition("/")
    branch = rest.rsplit("/", 1)[-1] if rest else ""
    allowed = policy["runtimes"].get(name)
    return allowed is not None and branch in allowed


def package_checks(facts: dict, upload: dict, submission: dict, policy: dict, luma_ids: set[str]) -> list[dict]:
    arch = upload.get("arch", "")
    label = f" ({arch})" if arch else ""
    out: list[dict] = []
    if upload.get("sha256") and facts.get("sha256") != upload["sha256"]:
        out.append(check(f"integrity{label}", False, "The file Depot received differs from the one you "
                         "uploaded. Please upload it again."))
        return out
    out.append(check(f"integrity{label}", True, "The file arrived whole."))
    if facts.get("refused"):
        out.append(check(f"format{label}", False, facts["refused"]))
        out.append(malware_check(facts, label))
        return out
    fmt = facts["format"]
    if fmt == "appimage":
        out.append(check(f"format{label}", False, "AppImage support is coming soon. For now, please send "
                         "a Flatpak bundle, or an .rpm or .deb package.",
                         detail=f"AppImage type 2, {facts.get('arch')}, AppRun={facts.get('has_apprun')}"))
        out.append(malware_check(facts, label))
        return out
    out.append(check(f"format{label}", True, {"flatpak": "A valid Flatpak bundle.",
                                              "rpm": "A valid RPM package.",
                                              "deb": "A valid Debian package."}[fmt]))
    # Identity
    app_id = facts.get("app_id", "")
    expected = submission.get("app_id") or app_id
    if fmt == "flatpak":
        if not APP_ID.fullmatch(app_id):
            out.append(check(f"identity{label}", False, f"“{app_id}” isn't a valid app id. Use reverse-DNS, "
                             "like com.example.Notes."))
        elif expected and app_id != expected:
            out.append(check(f"identity{label}", False, f"This bundle is for {app_id}, but the listing is for "
                             f"{expected}. Upload the bundle for {expected}."))
        else:
            problem = reserved(app_id, policy, luma_ids)
            out.append(check(f"identity{label}", problem is None, problem or f"App id {app_id}."))
    # Architecture
    actual = facts.get("arch", "")
    arch_ok = actual in ("x86_64", "aarch64") and (not arch or actual == arch) or (actual == "any" and fmt != "flatpak")
    if actual == "any":
        arch_message = "Runs on any processor."
    elif arch_ok:
        arch_message = f"Built for {actual}."
    elif actual in ("x86_64", "aarch64"):
        arch_message = f"This upload was marked {arch}, but the package is built for {actual}."
    else:
        arch_message = f"Depot accepts x86_64 and aarch64 apps; this package is built for {actual or 'an unknown processor'}."
    out.append(check(f"architecture{label}", arch_ok, arch_message))
    # Runtime
    if fmt == "flatpak":
        runtime = facts.get("runtime", "")
        ok = runtime_allowed(runtime, policy)
        names = ", ".join(f"{n} {'/'.join(b)}" for n, b in policy["runtimes"].items())
        out.append(check(f"runtime{label}", ok, f"Runtime {runtime}." if ok else
                         f"Your app is built on {runtime or 'no runtime'}, which Depot doesn't accept. "
                         f"Build it on one of: {names}."))
        if not facts.get("desktop_files"):
            out.append(check(f"launcher{label}", False, "Your app has no launcher. Export a .desktop file "
                             f"named {app_id}.desktop."))
    # Kernel modules and the like
    for refusal in facts.get("reach", {}).get("refusals", []):
        out.append(check(f"contents{label}", False, "Your package " + refusal + "."))
    out.append(malware_check(facts, label))
    return out


def malware_check(facts: dict, label: str) -> dict:
    scan = facts.get("scan") or {}
    if not scan.get("ran"):
        return check(f"malware{label}", False, "The malware scan couldn't run. Luma will look at this; "
                     "you don't need to do anything.", detail=scan.get("message", ""), internal=True)
    if scan.get("clean"):
        return check(f"malware{label}", True, "No malware found.", detail=scan.get("version", ""))
    return check(f"malware{label}", False, "The malware scan found something in your package. If you "
                 "think this is a mistake, reply to this review.", detail="; ".join(scan.get("found", [])))


def permission_checks(all_facts: list[dict], declared_raw) -> tuple[list[dict], dict]:
    """What the package can reach, for the reviewer. Developers no longer declare
    permissions: Depot reads them from the app's own sandbox settings, so nothing
    here fails. Anything still declared is kept in the summary for comparison."""
    declared = words.normalise_declared(declared_raw)
    detected: dict[str, list[str]] = {}
    notes: list[str] = []
    for facts in all_facts:
        reach = facts.get("reach") or {}
        for word, why in (reach.get("words") or {}).items():
            detected.setdefault(word, [])
            detected[word] += [w for w in why if w not in detected[word]]
        notes += [n for n in reach.get("notes", []) if n not in notes]
    listed = ", ".join(w for w in words.WORDS if w in detected) or "nothing beyond its own sandbox"
    out = [check("permissions", True, f"Your app reaches {listed}. Depot shows this to people, read from "
                                      "your app's sandbox settings.")]
    summary = {"declared": declared, "detected": {w: detected[w] for w in words.WORDS if w in detected},
               "declared_not_detected": [w for w in declared if w not in detected], "notes": notes}
    return out, summary


def media_checks(media: dict, listing: dict, kind: str) -> list[dict]:
    """media: {"icon": {"format","width","height"} | {"error"}, "screenshots": [{form, format, width, height} | {error}]}"""
    out = []
    summary = (listing.get("summary") or "").strip()
    if not summary:
        out.append(check("summary", False, "Add a one-line summary."))
    elif len(summary) > SUMMARY_MAX:
        out.append(check("summary", False, f"Your summary is {len(summary)} characters. Keep it to "
                         f"{SUMMARY_MAX} or fewer."))
    else:
        out.append(check("summary", True, "The summary fits on one line."))
    icon = media.get("icon")
    if not icon:
        out.append(check("icon", False, "Add an icon: an SVG, or a PNG at least 512 × 512."))
    elif icon.get("error"):
        out.append(check("icon", False, "Your icon " + icon["error"] + "."))
    elif icon["format"] == "svg":
        square = abs(icon["width"] - icon["height"]) <= 1
        out.append(check("icon", square, "The icon is an SVG." if square else "Your icon should be square."))
    elif icon["format"] in ("png", "webp"):
        ok = icon["width"] >= ICON_MIN and icon["width"] == icon["height"]
        out.append(check("icon", ok, f"The icon is {icon['width']} × {icon['height']}." if ok else
                         f"Your icon is {icon['width']} × {icon['height']}. Send an SVG, or a square PNG at "
                         f"least {ICON_MIN} × {ICON_MIN}."))
    shots = media.get("screenshots") or []
    desktop_ok = 0
    for index, shot in enumerate(shots, 1):
        form = shot.get("form", "desktop")
        key = f"screenshot:{index}"
        if shot.get("error"):
            out.append(check(key, False, f"Screenshot {index} " + shot["error"] + "."))
            continue
        if shot["format"] not in ("png", "webp"):
            out.append(check(key, False, f"Screenshot {index} should be a PNG or WebP."))
            continue
        w, h = shot["width"], shot["height"]
        if form == "phone":
            ok = (w, h) == PHONE_SIZE
            out.append(check(key, ok, f"Phone screenshot {index} is {w} × {h}." if ok else
                             f"Phone screenshot {index} is {w} × {h}. Phone screenshots are "
                             f"{PHONE_SIZE[0]} × {PHONE_SIZE[1]}."))
        else:
            ok = w >= DESKTOP_MIN[0] and h >= DESKTOP_MIN[1]
            desktop_ok += ok
            out.append(check(key, ok, f"Desktop screenshot {index} is {w} × {h}." if ok else
                             f"Desktop screenshot {index} is {w} × {h}. Desktop screenshots are at least "
                             f"{DESKTOP_MIN[0]} × {DESKTOP_MIN[1]}."))
    if kind == "app" and not desktop_ok and not any(s.get("form") == "desktop" and not s.get("error") for s in shots):
        out.append(check("screenshots", False, "Add at least one desktop screenshot of your app's window."))
    return out


def outcome(checks: list[dict]) -> str:
    """passed, needs_changes, or attention (only internal problems: Luma looks at it)."""
    failed = [c for c in checks if not c["ok"]]
    if not failed:
        return "passed"
    if all(c.get("internal") for c in failed):
        return "attention"
    return "needs_changes"
