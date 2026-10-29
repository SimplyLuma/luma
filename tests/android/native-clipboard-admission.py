#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile exact HIDL clipboard method bodies with explicit dependency fixtures.

This is a production-method policy test, not Android HwBinder/Wayland IPC proof.
A raw ordinary-APK HAL probe and trusted native control remain required.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

PREFIX=r'''
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <string>
#include <vector>
using uid_t=unsigned;
constexpr uid_t AID_SYSTEM=1000;
namespace android::hardware {
struct Status {
    static constexpr int EX_SECURITY=-1;
    int code;
    static Status fromExceptionCode(int code,const char*) { return {code}; }
};
struct IPCThreadState {
    static inline uid_t caller=0;
    static IPCThreadState* self() { static IPCThreadState value; return &value; }
    uid_t getCallingUid() { return caller; }
};
}
template<class T> struct Return {
    int code;
    Return():code(0) {}
    Return(android::hardware::Status status):code(status.code) {}
};
Return<void> Void() { return {}; }
using hidl_string=std::string;
int native_calls=0;
struct wl_data_source {};
const int data_source_listener=0;
const std::vector<std::string> MIME_TYPES={"text/plain"};
wl_data_source* wl_data_device_manager_create_data_source(void*) { ++native_calls; static wl_data_source source; return &source; }
void wl_data_source_add_listener(wl_data_source*,const int*,void* data) { ++native_calls; free(data); }
void wl_data_source_offer(wl_data_source*,const char*) { ++native_calls; }
void wl_data_device_set_selection(void*,wl_data_source*,unsigned) { ++native_calls; }
struct display { std::string clipboard="initial"; void* data_device=nullptr; void* data_device_manager=nullptr; unsigned keyboard_enter_serial=1; };
struct WaydroidClipboard {
    using getClipboardData_cb=std::function<void(const std::string&)>;
    display* mDisplay;
    Return<void> sendClipboardData(const hidl_string&);
    Return<void> getClipboardData(getClipboardData_cb);
};
'''
TEST=r'''
int main() {
    int cases=0;
    for (uid_t uid: {0u,1u,1000u,1001u,10000u,10131u,99000u,110000u}) {
        for (bool connected: {false,true}) {
            display state;
            if (connected) state.data_device=&state;
            WaydroidClipboard service{&state};
            android::hardware::IPCThreadState::caller=uid;
            native_calls=0;
            int callbacks=0;
            std::string returned;
            auto read=service.getClipboardData([&](const std::string& value) { ++callbacks; returned=value; });
            auto write=service.sendClipboardData("synthetic Android text");
            if(read.code || write.code || callbacks!=1) return 46;
            if (uid==AID_SYSTEM) {
                if(returned!="initial" || state.clipboard!="synthetic Android text" || (connected && native_calls!=4)) return 44;
            } else if(!returned.empty() || state.clipboard!="initial" || native_calls) {
                return 42;
            }
            cases+=2;
        }
    }
    return cases==32 ? 0 : 45;
}
'''


def methods(source):
    found=[]
    for name in ('sendClipboardData','getClipboardData'):
        start=source.index('Return<void> WaydroidClipboard::'+name+'(')
        brace=source.index('{',start);depth=1;end=brace+1
        while depth:
            if source[end]=='{':depth+=1
            elif source[end]=='}':depth-=1
            end+=1
        found.append(source[start:end])
    return '\n'.join(found)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('hardware_source',type=Path)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[2]
    original=(args.hardware_source/'hwcomposer/WaydroidClipboard.cpp').read_text()
    with tempfile.TemporaryDirectory() as directory:
        work=Path(directory);copy=work/'hwcomposer/WaydroidClipboard.cpp';copy.parent.mkdir()
        copy.write_text(original)
        shutil.copyfile(args.hardware_source/'hwcomposer/Android.bp',work/'hwcomposer/Android.bp')
        subprocess.run(['patch','--batch','--fuzz=0','-p1','-d',str(work),'-i',str(repo/'patches/android-hardware-waydroid/0015-luma-native-clipboard-caller-admission.patch')],check=True)
        for label,source,expected in [('patched',copy.read_text(),0),('unpatched',original,42)]:
            unit=work/(label+'.cpp');binary=work/label
            unit.write_text(PREFIX+methods(source)+TEST)
            subprocess.run(['c++','-std=c++17','-O1','-Wall','-Wextra','-Werror',str(unit),'-o',str(binary)],check=True)
            result=subprocess.run([str(binary)],timeout=5)
            assert result.returncode==expected,(label,result.returncode,expected)
        print('32 actual-method native HIDL read/write cases passed; unpatched negative control rejected with42; no IPC claim')


if __name__=='__main__':main()
