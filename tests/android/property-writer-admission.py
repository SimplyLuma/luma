#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile the patched production CheckPermissions and its unpatched control.

Supply an untouched, Waydroid-patched system/core tree. Files are copied to
private scratch; the source tree is never changed. Policy dependencies are
permissive fixtures, making the application-UID guard the deciding boundary.
This does not qualify a complete init binary or a running Android image.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


def function(source):
    start = source.index('uint32_t CheckPermissions(')
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


PREAMBLE = r'''
#include <sys/socket.h>
#include <cstdint>
#include <string>
#include <private/android_filesystem_config.h>
using std::string;
enum { PROP_SUCCESS, PROP_ERROR_INVALID_NAME, PROP_ERROR_HANDLE_CONTROL_MESSAGE,
       PROP_ERROR_PERMISSION_DENIED, PROP_ERROR_INVALID_VALUE };
bool IsLegalPropertyName(const string& n) { return n != "bad"; }
bool StartsWith(const string& n, const char* p) { return n.rfind(p, 0) == 0; }
bool CheckControlPropertyPerms(const string&, const string&, const string&, const ucred&) { return true; }
bool CheckMacPerms(const string&, const char*, const char*, const ucred&) { return true; }
bool CheckType(const char*, const string&) { return true; }
string StringPrintf(const char*, ...) { return "diagnostic"; }
struct Area { void GetPropertyInfo(const char*, const char** context, const char** type) {
    *context = "fixture"; *type = "string";
}} area;
Area* property_info_area = &area;
'''
TEST = r'''
int main() {
    for (uint32_t user : {0u, 1u, 2u, 100u}) {
        for (uint32_t app : {0u, 1000u, 1001u, 2000u, 9999u, 10000u, 10129u, 89999u, 99000u, 99999u}) {
            for (const char* name : {"luma.test.property_boundary", "persist.test.fixture", "ctl.start", "sys.powerctl"}) {
                ucred caller{}; caller.uid = user * AID_USER_OFFSET + app;
                string error;
                auto result = CheckPermissions(name, "fixture-only", "fixture", caller, &error);
                if (result != (app >= AID_APP_START ? PROP_ERROR_PERMISSION_DENIED : PROP_SUCCESS)) return 42;
                if (app >= AID_APP_START && error.empty()) return 43;
            }
        }
    }
    ucred caller{}; string error;
    return CheckPermissions("bad", "fixture", "fixture", caller, &error) == PROP_ERROR_INVALID_NAME ? 0 : 44;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('system_core', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    original = (args.system_core / 'init/property_service.cpp').read_text()
    with tempfile.TemporaryDirectory(prefix='luma-property-admission-') as temporary:
        work = Path(temporary)
        (work / 'init').mkdir()
        source = work / 'init/property_service.cpp'
        source.write_text(original)
        subprocess.run(['git', 'apply', '--unsafe-paths', '--directory=' + str(work),
                        str(root / 'patches/android-system-core/0001-luma-property-writer-admission.patch')],
                       cwd=work, check=True)
        for name, text, expected in (('patched', source.read_text(), 0), ('unpatched', original, 42)):
            unit = work / (name + '.cpp')
            unit.write_text(PREAMBLE + function(text) + TEST)
            executable = work / name
            subprocess.run(['g++', '-std=gnu++17', '-Wall', '-Werror',
                            '-I' + str(args.system_core.resolve() / 'libcutils/include'),
                            str(unit), '-o', str(executable)], check=True)
            result = subprocess.run([str(executable)])
            if result.returncode != expected:
                raise SystemExit(f'{name}: expected {expected}, got {result.returncode}')
        print('PASS: production property permission function, 160 UID/property cases; unpatched control rejected')


if __name__ == '__main__':
    main()
