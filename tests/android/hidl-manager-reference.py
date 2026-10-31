# SPDX-License-Identifier: Apache-2.0
"""Exercise the extracted upstream function with host shared-reference semantics.

This positive/negative concurrency control does not execute Android sp, Binder,
HALs or device libraries. Device codec/service stress remains required.
"""
from pathlib import Path
import argparse
import subprocess
import tempfile
import os
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', required=True, type=Path, help='prepared system/libhidl root')
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
repo = Path(__file__).resolve().parents[2]
src = args.source
base = args.output
base.mkdir(parents=True, exist_ok=True)
text=(src/'transport/ServiceManagement.cpp').read_text()
start=text.index('sp<IServiceManager1_2> defaultServiceManager1_2(bool useHostHwBinder) {')
end=text.index('\nstatic std::vector',start)
old=text[start:end]
with tempfile.TemporaryDirectory(dir=base) as tmp:
 root=Path(tmp);target=root/'transport/ServiceManagement.cpp';target.parent.mkdir()
 target.write_text(text)
 subprocess.run(['git','apply',str(repo/'patches/android-system-libhidl/0001-luma-copy-service-manager-under-lock.patch')],cwd=root,check=True)
 new=target.read_text();new=new[new.index('sp<IServiceManager1_2> defaultServiceManager1_2(bool useHostHwBinder) {'):];new=new[:new.index('\nstatic std::vector')]
 header=r'''
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
#include <atomic>
#include <iostream>
#include <chrono>
#include <cstdlib>
#include <unistd.h>
#define access(path, mode) 0
#define LOG(x) std::cerr
namespace android { namespace hidl { namespace manager { namespace V1_2 {
struct BnHwServiceManager{}; struct BpHwServiceManager{};
}}}}
struct IServiceManager1_2 { int value=42; };
template<class T> using sp=std::shared_ptr<T>;
struct ProcessState {
 static ProcessState* self(bool){static ProcessState s;return &s;}
 int getContextObject(void*){return 1;}
};
void waitForHwServiceManager(){}
template<class T,class Bp,class Bn> sp<T> fromBinder(int){return std::make_shared<T>();}
'''
 tail=r'''
int main(){
 std::atomic<bool> start{false};std::vector<std::thread> threads;
 for(int i=0;i<16;i++)threads.emplace_back([&]{
  while(!start.load())std::this_thread::yield();
  for(int j=0;j<20000;j++){
   auto s=defaultServiceManager1_2((j&1)!=0);
   if(!s){std::cerr<<"ERROR: manager became null outside its lock"<<std::endl;std::abort();}
   if(s->value!=42){std::cerr<<"ERROR: invalid retained manager"<<std::endl;std::abort();}
  }
 });
 start=true;for(auto& t:threads)t.join();
 std::cout<<"320000 concurrent manager references retained"<<std::endl;
}
'''
 for kind,body in [('original',old),('fixed',new)]:
  path=base/(kind+'.cpp');path.write_text(header+body+tail)
  binary=base/kind
  subprocess.run(['clang++','-std=c++17','-O1','-g','-pthread','-fsanitize=address','-fno-omit-frame-pointer',str(path),'-o',str(binary)],check=True)
  with (base/(kind+'.log')).open('w') as log:
   result=subprocess.run([str(binary)],stdout=log,stderr=subprocess.STDOUT,timeout=45,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0:abort_on_error=1'})
  print(kind,result.returncode,flush=True)
  if kind=='original':assert result.returncode!=0,'Positive race control did not reproduce; no fixed-only pass'
  else:assert result.returncode==0,'Fixed code still races'
print('PASS_HOST_EXTRACTED_FUNCTION_RACE_CONTROL; Android library/device execution still required')
