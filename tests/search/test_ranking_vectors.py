# SPDX-License-Identifier: Apache-2.0
"""Luma Search ranking vectors against the Python twin (luma-search).

python3 tests/search/test_ranking_vectors.py [--dump]
"""

import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/luma-search"))

from luma_search import ranking as R  # noqa: E402

VECTORS = json.loads((Path(__file__).with_name("ranking-vectors.json")).read_text())
SETTINGS = json.loads((Path(__file__).with_name("settings-cases.json")).read_text())
PAGES = json.loads((ROOT / "src/luma-search/settings-pages.json").read_text())["pages"]
CONTEXT = dict(home=VECTORS["home"], now=VECTORS["now"], xdg_folders=frozenset(VECTORS["xdg"]))


def ranked(query, limit=50):
    return R.rank_files(VECTORS["files"], R.split_terms(query), limit=limit, **CONTEXT)


class RankingVectors(unittest.TestCase):
    def test_cases(self):
        for case in VECTORS["cases"]:
            with self.subTest(query=case["query"]):
                terms = R.split_terms(case["query"])
                files = ranked(case["query"])
                paths = [item["path"] for item in files]
                why = " | ".join(f"{i['path']}={i['score']}{list(i['evidence'])}" for i in files)
                if "files_first" in case:
                    self.assertEqual(paths[: len(case["files_first"])], case["files_first"], why)
                for absent in case.get("files_absent", []):
                    self.assertNotIn(absent, paths, why)
                for first, second in case.get("files_before", []):
                    self.assertIn(first, paths, why)
                    if second in paths:
                        self.assertLess(paths.index(first), paths.index(second), why)
                if "top" in case:
                    entries = [(item["score"], 2, i, f"file:{item['path']}") for i, item in enumerate(files)]
                    for i, app in enumerate(VECTORS["apps"]):
                        score = R.score_app(app["name"], terms, generic_name=app["genericName"],
                                            keywords=app["keywords"])
                        if score is not None:
                            entries.append((score, 0, i, f"app:{app['id']}"))
                    entries.sort(key=lambda e: (-e[0], e[1], e[2]))
                    self.assertEqual(entries[0][3], case["top"], entries[:4])

    def test_settings_pages(self):
        for case in SETTINGS["cases"]:
            with self.subTest(query=case["query"]):
                ranked = R.rank_setting_pages(PAGES, R.split_terms(case["query"]))
                first = ranked[0]["page"]["id"] if ranked else None
                self.assertEqual(first, case["first"], [(i["page"]["id"], i["score"]) for i in ranked])
        for item in SETTINGS["names"]:
            self.assertEqual(R.match_name(item["name"], R.split_terms(item["query"])).tier, item["tier"])
        for page in PAGES:
            self.assertTrue(page.get("panel") or (page.get("desktop") and page.get("action")), page)

    def test_pieces(self):
        self.assertEqual(R.fold_text("Ångström Café"), "angstrom cafe")
        self.assertEqual(R.name_words("MyDocuments_v2-final"), ["my", "documents", "v", "2", "final"])
        self.assertEqual(R.match_name("report.pdf", ["report"], ignore_extension=True).tier, R.TIER_EXACT)
        self.assertEqual(R.match_name("Documents", ["documnets"]).tier, R.TIER_FUZZY)
        self.assertIsNone(R.match_name("Dots", ["dost"]))
        self.assertEqual(R.junk_penalty("backup", ["backup"])[0], 0)
        self.assertIn("date", R.junk_penalty("IMG_20240512_101010.jpg")[1])
        self.assertEqual(R.fts_query(["depot-18", "Backup"]), "depot* 18* Backup*")
        self.assertEqual(R.fts_query(["documnets"], shorten=True), "docu*")

    def test_hundred_thousand_candidates(self):
        now = VECTORS["now"]
        big = []
        for i in range(100000):
            directory = f"/home/ada/Projects/p{i % 97}/src/m{i % 13}"
            name = f"depot-{i}-backup.tar" if i % 7 == 0 else f"Report {i}.pdf" if i % 5 == 0 else f"note-{i}.md"
            big.append({"path": f"{directory}/{name}", "name": name, "modified": now - i * 60})
        big.append({"path": "/home/ada/depot", "name": "depot"})
        started = time.monotonic()
        top = R.rank_files(big, ("depot",), limit=20, **CONTEXT)
        elapsed = time.monotonic() - started
        self.assertEqual(top[0]["path"], "/home/ada/depot")
        self.assertLess(elapsed, 10.0)


def dump():
    out = {}
    for case in VECTORS["cases"]:
        out[case["query"]] = [[i["path"], i["score"]] for i in ranked(case["query"])]
        terms = R.split_terms(case["query"])
        out[case["query"]] += [[f"app:{a['id']}", R.score_app(a["name"], terms, generic_name=a["genericName"],
                                                            keywords=a["keywords"])] for a in VECTORS["apps"]]
    for case in SETTINGS["cases"]:
        out["settings:" + case["query"]] = [[i["page"]["id"], i["score"]]
                                            for i in R.rank_setting_pages(PAGES, R.split_terms(case["query"]))]
    print(json.dumps(out, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    if "--dump" in sys.argv:
        dump()
    else:
        unittest.main()
