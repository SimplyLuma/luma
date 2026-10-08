#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Run the Ari evaluation suite (brief §11) against one installed model.

Ari runs in this process with its own model runtime. Settings go to GSettings'
memory backend and app launches and display changes are dry runs, so a machine
someone is using is not changed. Results are written as JSON beside the cases.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ["GSETTINGS_BACKEND"] = "memory"
os.environ["ARI_EVAL_DRY_RUN"] = "1"
scratch = Path(tempfile.mkdtemp(prefix="ari-eval-"))
os.environ.setdefault("XDG_STATE_HOME", str(scratch / "state"))

from ari import agent as agent_module, audit, hardware, models, paths  # noqa: E402
from ari.agent import Agent, Tools, Turn  # noqa: E402
from ari.policy import Policy  # noqa: E402
from ari.providers import OpenAICompatible  # noqa: E402
from ari.runtime import LocalRuntime  # noqa: E402
from ari.store import Store  # noqa: E402


def sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def score(case: dict, text: str, tools: list[dict], steps: list[dict], events: list[dict]) -> list[str]:
    problems = []
    names = [t["tool"] for t in tools if t.get("decision") == "ran"]
    for tool in case.get("tools", []):
        if tool not in names:
            problems.append(f"expected tool {tool}, ran {names or 'none'}")
    if case.get("tools") == [] and names:
        problems.append(f"expected no tools, ran {names}")
    for pattern in case.get("never_tools", []):
        regex = re.compile(pattern.replace("*", ".*"))
        if any(regex.fullmatch(n) for n in names):
            problems.append(f"ran forbidden tool matching {pattern}")
    for key, value in (case.get("arguments") or {}).items():
        if not any(t.get("arguments", {}).get(key) == value for t in tools if t["tool"] in case["tools"]):
            problems.append(f"expected {key}={value}")
    for fact in case.get("facts", []):
        if fact.lower() not in text.lower():
            problems.append(f"missing fact {fact!r}")
    if case.get("facts_any") and not any(f.lower() in text.lower() for f in case["facts_any"]):
        problems.append(f"said none of {case['facts_any']}")
    for phrase in case.get("forbidden", []):
        if phrase.lower() in text.lower():
            problems.append(f"said forbidden {phrase!r}")
    for start in case.get("forbidden_start", []):
        if text.lower().startswith(start.lower()):
            problems.append(f"claimed success ({start}) without a step")
    if case.get("step") and not steps:
        problems.append("no step card")
    if case.get("no_step") and steps:
        problems.append("made a step it shouldn't have")
    if case.get("approval") and not any(e.get("type") == "approval" for e in events):
        problems.append("didn't ask first")
    if case.get("approval") is False and any(e.get("type") == "approval" for e in events):
        problems.append("asked when it shouldn't")
    if case.get("max_sentences") and sentences(text) > case["max_sentences"]:
        problems.append(f"too long ({sentences(text)} sentences)")
    if "!" in text:
        problems.append("used an exclamation mark")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    catalogue = models.catalogue()
    model = models.find(args.model, catalogue)
    if model not in models.installed(catalogue):
        print(f"{args.model} isn't installed")
        return 2
    machine = hardware.detect()
    runtime = LocalRuntime(machine)
    runtime.ensure(model)
    store = Store(scratch / "eval.db")
    tool_dir = HERE.parent / "ari" / "tools"
    from ari.service import TOOL_SERVERS
    tools = Tools({name: [sys.executable, str(tool_dir / f"{name}.py")] for name in TOOL_SERVERS})
    tools.start()

    def provider():
        client = OpenAICompatible(runtime.base_url, runtime.key, model.id,
                                  extra={"chat_template_kwargs": {"enable_thinking": False}})
        client.hardware_summary = machine.summary()
        return client, f"{model.name} {model.quantisation}", "on this machine", model.knowledge_cutoff, model.profile
    ari = Agent(store, tools, Policy(), model_provider=provider)
    ari.describe_model = lambda: f"{model.name} {model.quantisation} on this machine"
    audit_file = scratch / "activity.jsonl"
    paths.audit_path = lambda: audit_file  # the suite's activity stays with the suite
    cases = json.loads((HERE / "cases.json").read_text())["cases"]
    results = []
    for case in cases:
        if args.only and case["id"] not in args.only:
            continue
        events: list[dict] = []
        conversation = store.new_conversation()
        ari.policy.approval_mode = case.get("approval_mode", "system")

        def emit(event: dict) -> None:
            events.append(event)
            if event["type"] == "approval":  # the suite answers approval cards as the case says
                ari.approve(event["approval"], case.get("approve", True))
        started = time.monotonic()
        for earlier in case.get("before", []):
            ari.answer(Turn(conversation, earlier, lambda _e: None))
        before = len(audit.recent(10000, audit_file))
        turn = Turn(conversation, case["prompt"], emit)
        ari.answer(turn)
        elapsed = time.monotonic() - started
        text = next((e["text"] for e in reversed(events) if e["type"] == "done"), "")
        ran = audit.recent(10000, audit_file)[before:]
        steps = [e for e in events if e["type"] == "step"]
        problems = score(case, text, ran, steps, events)
        if case.get("undo") and steps and not ari.undo(steps[0]["step"]):
            problems.append("undo failed")
        results.append({"id": case["id"], "prompt": case["prompt"], "answer": text, "seconds": round(elapsed, 1),
                        "tools": [{k: t.get(k) for k in ("tool", "decision", "arguments", "ok")} for t in ran],
                        "passed": not problems, "problems": problems})
        mark = "PASS" if not problems else "FAIL"
        print(f"{mark} {case['id']:12} {elapsed:5.1f}s  {text[:90]!r}  {'; '.join(problems)}")
    runtime.stop()
    tools.stop()
    shutil.rmtree(scratch, ignore_errors=True)
    passed = sum(r["passed"] for r in results)
    report = {"model": model.id, "machine": machine.as_dict(), "date": time.strftime("%Y-%m-%d"),
              "passed": passed, "total": len(results), "results": results}
    out = HERE / "results" / f"{time.strftime('%Y-%m-%d')}-{model.id}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"{passed}/{len(results)} passed · {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
