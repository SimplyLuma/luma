#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Execute the pinned libhwbinder pool configuration with both real call sites.

The Binder ioctl is a recording stub; the fatal checks, allocation arithmetic,
locking, and state updates are the unmodified Android implementation. This is
an initialization regression control, not installed Binder qualification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import tempfile

p = argparse.ArgumentParser()
p.add_argument("process_state", type=Path)
p.add_argument("service", type=Path)
p.add_argument("module", type=Path)
a = p.parse_args()
text = a.process_state.read_text()
start = text.index("status_t ProcessState::setThreadPoolConfiguration(")
end = text.index("\nstatus_t ProcessState::enableOnewaySpamDetection", start)
body = text[start:end]
def call(path):
    matches = re.findall(r"configureRpcThreadpool\((\d+),\s*(true|false)", path.read_text())
    if len(matches) != 1:
        raise ValueError("Expected one maintained pool initializer")
    return int(matches[0][0]), matches[0][1]
service = call(a.service)
module = call(a.module)
assert service == module == (4, "true"), (service, module)
prefix = r'''
#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
using status_t = int;
constexpr int NO_ERROR=0, BINDER_SET_MAX_THREADS=42;
size_t recordedKernelThreads;
int ioctl(int, int op, size_t* value) { assert(op==BINDER_SET_MAX_THREADS); recordedKernelThreads=*value; return 0; }
#define LOG_ALWAYS_FATAL_IF(test, message) do { if(test) { std::fputs(message,stderr); std::abort(); } } while(0)
#define ALOGE(...) std::fprintf(stderr,__VA_ARGS__)
using AutoMutex=std::lock_guard<std::mutex>;
struct ProcessState {
 bool mThreadPoolStarted=false, mSpawnThreadOnStart=false;
 size_t mMaxThreads=0;
 int mDriverFD=0;
 std::mutex mLock;
 status_t setThreadPoolConfiguration(size_t,bool);
};
'''
main = r'''
int main(int argc,char** argv) {
 assert(argc==2); const int mode=std::atoi(argv[1]); ProcessState state;
 // Service prepares its pool before loading the conventional HAL module.
 assert(state.setThreadPoolConfiguration(4,true)==0);
 if(mode==0 || mode==1) state.mThreadPoolStarted=true;
 assert(state.setThreadPoolConfiguration(mode==0?1:4,true)==0);
 if(mode==2) state.mThreadPoolStarted=true;
 // Both registration orders retain the service's native four-thread budget.
 assert(state.mMaxThreads==4 && state.mSpawnThreadOnStart && recordedKernelThreads==2);
 assert(state.setThreadPoolConfiguration(4,true)==0);
 assert(state.mMaxThreads==4);
}
'''
with tempfile.TemporaryDirectory(prefix="luma-composer-rpc-control-") as tmp:
    root = Path(tmp)
    cpp = root / "control.cpp"
    cpp.write_text(prefix + body + main)
    binary = root / "control"
    subprocess.run(["g++", "-std=c++17", "-pthread", str(cpp), "-o", str(binary)], check=True)
    results = []
    for mode in (0, 1, 2):
        result = subprocess.run([str(binary), str(mode)], capture_output=True)
        expected = -signal.SIGABRT if mode == 0 else 0
        assert result.returncode == expected, (mode, result.returncode, result.stderr)
        if mode == 0:
            assert b"Binder threadpool cannot be shrunk after starting" in result.stderr
        results.append({"mode": mode, "exit": result.returncode, "stderr": result.stderr.decode()})
print(json.dumps({"result": "PASS", "service_call": service, "module_call": module,
                  "actual_upstream_function_sha256": hashlib.sha256(body.encode()).hexdigest(),
                  "inputs": {str(x): hashlib.sha256(x.read_bytes()).hexdigest() for x in (a.process_state, a.service, a.module)},
                  "real_function_controls": results,
                  "installed_Binder_and_restart_qualification_not_inferred": True}, indent=2))
