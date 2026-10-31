#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Test complete task HAL source against explicit Binder/HIDL dependencies.

Production method execution and an unpatched negative control; not actual IPC.
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

PREFIX = r'''
#include <atomic>
#include <cstdint>
#include <cstring>
#include <functional>
#include <memory>
#include <string>
#include <thread>
#include <vector>
#define __ANDROID_API__ 33
#define ALOGE(...) ((void)0)
constexpr unsigned AID_SYSTEM=1000;
namespace android {
using String16=std::string;
struct String8:std::string { using std::string::string; String8(const std::string& s):std::string(s){} const char* string()const{return c_str();} };
template<class T> using sp=std::shared_ptr<T>;
struct IBinder { virtual ~IBinder()=default; };
struct Status { bool ok; bool isOk() const{return ok;} int exceptionCode()const{return -1;} };
namespace hardware {
struct IPCThreadState { static inline thread_local unsigned caller=0; static IPCThreadState* self(){static IPCThreadState s;return &s;} unsigned getCallingUid(){return caller;} };
using hidl_string=std::string;
template<class T> struct Return { bool isOk()const{return true;} };
inline Return<void> Void(){return {};}
}
namespace app {
struct IActivityTaskManager:IBinder {
 std::atomic<int> focus{0},remove{0},all{0},resize{0}; bool healthy=true;
 void setFocusedTask(uint32_t){++focus;}
 void removeTask(uint32_t,bool* result){++remove;*result=healthy;}
 void removeAllVisibleRecentTasks(){++all;}
 Status resizeTask(uint32_t,int32_t,int32_t,int32_t,int32_t,int32_t,bool* result){++resize;*result=true;return {healthy};}
};
}
struct ServiceManager {
 std::atomic<int> lookups{0};
 sp<IBinder> task,platform;
 sp<IBinder> checkService(const String16& name){++lookups;return name=="activity_task"?task:platform;}
 sp<IBinder> getService(const String16& name){return checkService(name);}
};
inline sp<ServiceManager> registry=std::make_shared<ServiceManager>();
inline sp<ServiceManager> defaultServiceManager(){return registry;}
template<class T> sp<T> interface_cast(const sp<IBinder>& b){return std::dynamic_pointer_cast<T>(b);}
}
namespace lineageos::waydroid {
struct IPlatform:android::IBinder {
 std::atomic<int> names{0};
 void getAppName(const android::String16&,android::String16* out){++names;*out="synthetic-label";}
};
}
namespace vendor::waydroid::task::V1_0::implementation {
using namespace android::hardware;
using android::sp;using android::IBinder;
using android::app::IActivityTaskManager;using lineageos::waydroid::IPlatform;
struct WaydroidTask {
 using getAppName_cb=std::function<void(const std::string&)>;
 Return<void> setFocusedTask(uint32_t);Return<void> removeTask(uint32_t);
 Return<void> removeAllVisibleRecentTasks();Return<void> getAppName(const hidl_string&,getAppName_cb);
 bool resizeTaskNative(uint32_t,int32_t,int32_t,int32_t,int32_t,int32_t);
 // Present only to compile the original negative control. Patched source/header
 // are checked to contain neither shared cache member.
 sp<IActivityTaskManager> mActivityTaskManager;sp<IPlatform> mPlatform;
};
}
'''
TEST = r'''
int main(){
 using namespace android;
 using vendor::waydroid::task::V1_0::implementation::WaydroidTask;
 using lineageos::waydroid::IPlatform;
 int cases=0;
 for(unsigned uid:{0u,1u,1000u,1001u,10000u,10134u,99000u,110000u}){
  for(bool available:{false,true}){
   registry=std::make_shared<ServiceManager>();
   auto task=std::make_shared<app::IActivityTaskManager>();auto platform=std::make_shared<IPlatform>();
   if(available){registry->task=task;registry->platform=platform;}
   WaydroidTask service;hardware::IPCThreadState::caller=uid;
   int callbacks=0;std::string value;
   if(!service.setFocusedTask(7).isOk()||!service.removeTask(7).isOk()||!service.removeAllVisibleRecentTasks().isOk())return 41;
   if(!service.getAppName("fixture.package",[&](auto s){++callbacks;value=s;}).isOk()||callbacks!=1)return 41;
   if(uid!=AID_SYSTEM){
    if(registry->lookups||task->focus||task->remove||task->all||platform->names||!value.empty())return 42;
   }else{
    if(registry->lookups!=4)return 43;
    if(available&&(task->focus!=1||task->remove!=1||task->all!=1||platform->names!=1||value!="synthetic-label"))return 44;
    if(!available&&value!="fixture.package")return 44;
   }
   cases+=4;
  }
 }
 registry=std::make_shared<ServiceManager>();WaydroidTask service;hardware::IPCThreadState::caller=AID_SYSTEM;
 auto first=std::make_shared<app::IActivityTaskManager>();registry->task=first;service.removeTask(7);
 auto second=std::make_shared<app::IActivityTaskManager>();registry->task=second;service.removeTask(7);
 if(first->remove!=1||second->remove!=1)return 45;
 registry->task=nullptr;service.removeTask(7);if(second->remove!=1)return 45;
 registry->task=second;second->healthy=false;
 if(service.resizeTaskNative(7,0,0,100,100,0)||second->resize!=1)return 46;
 second->healthy=true;if(!service.resizeTaskNative(7,0,0,100,100,0)||second->resize!=2)return 46;
 auto p1=std::make_shared<IPlatform>();registry->platform=p1;std::string value;
 service.getAppName("fixture.package",[&](auto s){value=s;});
 auto p2=std::make_shared<IPlatform>();registry->platform=p2;
 service.getAppName("fixture.package",[&](auto s){value=s;});if(p1->names!=1||p2->names!=1)return 47;
 std::vector<std::thread> workers;
 for(int i=0;i<4;++i)workers.emplace_back([&]{hardware::IPCThreadState::caller=AID_SYSTEM;for(int j=0;j<200;++j){service.removeTask(7);service.resizeTaskNative(7,0,0,100,100,0);}});
 for(auto& t:workers)t.join();
 if(second->remove!=801||second->resize!=802)return 48;
 registry=nullptr;
 service.removeTask(7);service.getAppName("fixture.package",[&](auto s){value=s;});
 if(value!="fixture.package"||cases!=64)return 49;
 return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('hardware_source', type=Path)
    parser.add_argument('--compiler', default='c++')
    parser.add_argument('--sanitize', choices=['address,undefined', 'thread'])
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    relative = Path('interfaces/task/1.0/default')
    with tempfile.TemporaryDirectory(prefix='luma-native-task-') as directory:
        work = Path(directory)
        shutil.copytree(args.hardware_source / relative, work / relative)
        original = (work / relative / 'WaydroidTask.cpp').read_text()
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(work), '-i',
                        str(repo / 'patches/android-hardware-waydroid/0017-luma-native-task-caller-admission.patch')], check=True)
        patched = (work / relative / 'WaydroidTask.cpp').read_text()
        header = (work / relative / 'WaydroidTask.h').read_text()
        assert all(name not in patched + header for name in ['mActivityTaskManager', 'mPlatform'])
        assert 'libhwbinder_headers' in (work / relative / 'Android.bp').read_text()
        for label, source, expected in [('patched', patched, 0), ('unpatched', original, 42)]:
            unit = work / (label + '.cpp'); binary = work / label
            unit.write_text(PREFIX + re.sub(r'^#include[^\n]*\n', '', source, flags=re.M) + TEST)
            command = [args.compiler, '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror', '-pthread']
            if args.sanitize:
                command += ['-fsanitize=' + args.sanitize, '-fno-omit-frame-pointer']
            subprocess.run(command + [str(unit), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], timeout=20)
            assert result.returncode == expected, (label, result.returncode, expected)
        print('PASS:64 task method cases; current-service resolution, no replay, concurrent resize/HIDL; unpatched control42. No IPC claim.')


if __name__ == '__main__':
    main()
