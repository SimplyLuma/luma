#!/bin/bash
# The Shell (JS) and luma-search (Python) ranking twins give identical scores.
# tests/search/parity.sh path/to/lumaSearchRanking.js
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
js=$(node "$here/ranking.mjs" "$1" --dump)
py=$(python3 "$here/test_ranking_vectors.py" --dump)
if [ "$(printf '%s' "$js" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), sort_keys=True))')" != \
     "$(printf '%s' "$py" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), sort_keys=True))')" ]; then
    diff <(printf '%s' "$js" | python3 -m json.tool --sort-keys) <(printf '%s' "$py" | python3 -m json.tool --sort-keys) | head -40
    echo "ranking parity: FAIL"; exit 1
fi
echo "ranking parity: PASS"
