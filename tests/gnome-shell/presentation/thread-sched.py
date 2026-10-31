# SPDX-License-Identifier: Apache-2.0
"""Print a thread's scheduling policy, flags and utilization clamps as JSON
(sched_getattr): thread-sched.py TID, or thread-sched.py self."""
import ctypes
import json
import os
import platform
import struct
import sys

SYS_SCHED_GETATTR = {'aarch64': 275, 'x86_64': 315}[platform.machine()]
libc = ctypes.CDLL(None, use_errno=True)
tid = os.getpid() if sys.argv[1] == 'self' else int(sys.argv[1])
buf = ctypes.create_string_buffer(56)
if libc.syscall(SYS_SCHED_GETATTR, tid, buf, 56, 0) != 0:
    sys.exit(f'sched_getattr: {os.strerror(ctypes.get_errno())}')
_size, policy, flags, nice, _prio, _rt, _dl, _period, util_min, util_max = struct.unpack('IIQiIQQQII', buf.raw)
print(json.dumps({'policy': policy, 'reset_on_fork': bool(flags & 1), 'nice': nice,
                  'util_min': util_min, 'util_max': util_max}))
