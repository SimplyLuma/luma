#!/usr/bin/env python3
"""Reject a packaged unlock prompt activated before its transition targets."""
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text()
start = source.index('class UnlockDialog extends')
end = source.index('    vfunc_key_press_event(', start)
constructor = source[start:end]
activation = constructor.rindex('this._showPrompt();')
assert constructor.count('this._showPrompt();') == 2  # scroll handler + final activation
for target in ('this._otherUserButton =', 'this._idleWatchId =', "this.connect('destroy'"):
    assert constructor.index(target) < activation, target
# 0290 ends the constructor with a compact-login branch whose else arm is
# the same final activation.
assert constructor[activation:].strip() in (
    'this._showPrompt();\n    }',
    'this._showPrompt();\n        }\n    }',
)
