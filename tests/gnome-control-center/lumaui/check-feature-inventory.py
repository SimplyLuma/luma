#!/usr/bin/env python3
"""Check declared Settings scope; incomplete implementation deliberately fails.

The inventory is a review artifact, not evidence of behavior or pixel parity.
Its states must cover the fixture and scenario; each page remains open until
its controls, real adapters, tests and four measured variants are evidenced.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument('--scope-only', action='store_true')
args = parser.parse_args()
fixture = json.loads((ROOT / 'tests/fixtures/settings-v70.json').read_text())
scenario = json.loads((ROOT / 'tools/lumaui-conform/scenarios/settings.json').read_text())
inventory = json.loads(Path(__file__).with_name('feature-inventory.json').read_text())
expected = {p['id'] for p in fixture['pages']} | {'app:' + a for a in fixture['data']['CFAPPS']}
actual = [page['id'] for page in inventory['pages']]
assert len(actual) == len(set(actual)) and set(actual) == expected, 'Inventory must name every page once'
assert inventory['scenario_states'] == [s['name'] for s in scenario['states']], 'Inventory must name every scenario state'
assert set(scenario['phone_states']) == set(inventory['scenario_states']), 'Every state needs phone coverage'
for page in inventory['pages']:
    assert page['features'] and page['fixture_ui'] and page['live_adapter'] and page['visual_gate'], page['id']
assert inventory['shared_features'], 'Chrome and cross-page behavior must be inventoried'
print(f"Scope declared: {len(actual)} pages, {len(inventory['scenario_states'])} states, {len(inventory['shared_features'])} shared features")
if args.scope_only:
    print('Scope declaration only; this is not a feature-completion or conform PASS')
    sys.exit(0)
open_pages = [p['id'] for p in inventory['pages'] if any(p[k] != 'evidenced-complete' for k in ('fixture_ui', 'live_adapter', 'visual_gate'))]
if open_pages or any(item['status'] != 'evidenced-complete' for item in inventory['shared_features']):
    print('NOT FINISHED: ' + ', '.join(open_pages))
    sys.exit(1)
for page in inventory['pages']:
    assert page.get('evidence'), f"No completion evidence: {page['id']}"
print('Declared completion evidence present; review the linked behavior tests and conform reports separately')
