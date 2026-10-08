"""Budget a complete conform variant from its required Studio settling waits.

The baseline covers ordinary capture, GTK and comparison work. Explicit
scenario waits are additional work that a complete --all run must retain.
"""

import json
import math
import sys


BASE_SECONDS = 900
MAX_SECONDS = 3600


def budget(scenario):
    before = scenario.get("spec", {}).get("before", [])
    states = scenario.get("states", [])
    waits = 0.0
    for state in states:
        for action in [*before, *state.get("spec", [])]:
            if "wait" not in action:
                continue
            value = action["wait"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid spec wait: {value!r}")
            waits += value / 1000
    result = BASE_SECONDS + math.ceil(waits)
    if result > MAX_SECONDS:
        raise ValueError(f"required waits need {result}s, above the {MAX_SECONDS}s variant ceiling")
    return result


if __name__ == "__main__":
    try:
        print(budget(json.load(open(sys.argv[1], encoding="utf-8"))))
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"lumaui-conform: invalid variant budget: {error}")
