#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Execute the production task-channel handler with real Unix packet sockets.

A private user namespace supplies actual SO_PEERCRED UID1000 or10000 without
changing host users. The framework resize implementation is an explicit fixture.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

PREFIX = r'''
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#include <cstdint>
#include <cassert>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <memory>
#include <thread>
#include <vector>
#include <iostream>
namespace android {template<class T>using sp=std::shared_ptr<T>;}
struct WaydroidTask {
 int calls=0;
 bool resizeTaskNative(uint32_t id,int32_t left,int32_t top,int32_t right,int32_t bottom,int32_t mode){
  ++calls;assert(id==7&&left==0&&top==0&&right==100&&bottom==100&&mode==0);return true;
 }
};
'''
TEST = r'''
int main(){
 using Clock=std::chrono::steady_clock;
 auto service=std::make_shared<WaydroidTask>();
 const PrairieResizeRequest valid{kPrairieResizeMagic,kPrairieResizeVersion,0,7,0,0,100,100,0};
 if(getuid()!=1000){
  assert(getuid()==10000);int fds[2];assert(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,fds)==0);
  assert(send(fds[1],&valid,sizeof(valid),MSG_NOSIGNAL)==sizeof(valid));
  handlePrairieResizeClient(service,fds[0]);assert(service->calls==0);
  close(fds[0]);close(fds[1]);std::cout<<"PASS actual untrusted SO_PEERCRED; no framework call\n";return 0;
 }
 for(int scenario=0;scenario<10;++scenario){
  int fds[2];assert(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,fds)==0);
  auto request=valid;
  if(scenario==1)request.reserved=1;
  if(scenario==2)request.magic=0;
  if(scenario==3)request.version=99;
  if(scenario==4)request.right=0;
  std::vector<char> payload(sizeof(request));memcpy(payload.data(),&request,sizeof(request));
  if(scenario==5)payload.push_back('x');
  if(scenario==6)payload.pop_back();
  if(scenario==9){
   int small=4096;assert(setsockopt(fds[0],SOL_SOCKET,SO_SNDBUF,&small,sizeof(small))==0);
   char fill[1024]{};while(send(fds[0],fill,sizeof(fill),MSG_DONTWAIT|MSG_NOSIGNAL)>=0){}
   assert(errno==EAGAIN||errno==EWOULDBLOCK);
  }
  if(scenario!=7&&scenario!=8)assert(send(fds[1],payload.data(),payload.size(),MSG_NOSIGNAL)==static_cast<ssize_t>(payload.size()));
  if(scenario==8){close(fds[1]);fds[1]=-1;}
  int previous=service->calls;auto start=Clock::now();
  handlePrairieResizeClient(service,fds[0]);
  auto elapsed=std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now()-start).count();
  assert(elapsed<1500);
  if(scenario==7||scenario==9)assert(elapsed>=200);
  assert(service->calls==previous+((scenario==0||scenario==9)?1:0));
  if(scenario!=8&&scenario!=9){
   PrairieResizeReply reply{};assert(recv(fds[1],&reply,sizeof(reply),MSG_DONTWAIT)==sizeof(reply));
   assert(reply.magic==kPrairieResizeMagic&&reply.version==kPrairieResizeVersion&&reply.reserved==0);
   assert(reply.success==(scenario==0));
  }
  close(fds[0]);if(fds[1]>=0)close(fds[1]);
 }
 assert(service->calls==2);
 std::cout<<"PASS actual trusted SO_PEERCRED; valid/malformed/oversize/short/stalled/closed/backpressured sockets\n";
}
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('hardware_source',type=Path)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[2]
    relative=Path('interfaces/task/1.0/default/service.cpp')
    with tempfile.TemporaryDirectory(prefix='luma-task-channel-') as directory:
        work=Path(directory);target=work/relative;target.parent.mkdir(parents=True)
        shutil.copyfile(args.hardware_source/relative,target)
        original=target.read_text()
        subprocess.run(['patch','--batch','--fuzz=0','-p1','-d',str(work),'-i',str(repo/'patches/android-hardware-waydroid/0018-luma-bound-task-channel-io.patch')],check=True)
        source=target.read_text()
        start=source.index('constexpr uint32_t kPrairieResizeMagic')
        end=source.index('void servePrairieResizeRequests(')
        unit=work/'channel.cpp';binary=work/'channel'
        unit.write_text(PREFIX+source[start:end]+TEST)
        subprocess.run(['c++','-std=c++17','-O1','-Wall','-Wextra','-Werror','-pthread',str(unit),'-o',str(binary)],check=True)
        for uid in (1000,10000):
            subprocess.run(['unshare','--user','--map-user='+str(uid),'--map-group='+str(uid),str(binary)],check=True,timeout=10)
        # Preserve the original handler body and require it to accept the
        # oversized prefix, proving this regression test reaches the defect.
        constants=original[original.index('constexpr uint32_t kPrairieResizeMagic'):original.index('void servePrairieResizeRequests(')]
        start=original.index('        ucred credentials{};')
        end=original.index('        close(client);',start)
        old_handler='void handlePrairieResizeClient(const android::sp<WaydroidTask>& service, int client) {\n'+original[start:end]+'}\n'
        negative=r'''
int main(){
 assert(getuid()==1000);int fds[2];assert(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,fds)==0);
 auto service=std::make_shared<WaydroidTask>();
 PrairieResizeRequest request{kPrairieResizeMagic,kPrairieResizeVersion,0,7,0,0,100,100,0};
 std::vector<char> payload(sizeof(request)+1);memcpy(payload.data(),&request,sizeof(request));
 assert(send(fds[1],payload.data(),payload.size(),MSG_NOSIGNAL)==static_cast<ssize_t>(payload.size()));
 handlePrairieResizeClient(service,fds[0]);close(fds[0]);close(fds[1]);return service->calls==1?42:43;
}
'''
        unit.write_text(PREFIX+constants+old_handler+negative)
        subprocess.run(['c++','-std=c++17','-O1','-Wall','-Wextra','-Werror','-pthread',str(unit),'-o',str(binary)],check=True)
        result=subprocess.run(['unshare','--user','--map-user=1000','--map-group=1000',str(binary)],timeout=10)
        assert result.returncode==42,('unpatched oversize negative',result.returncode)
        print('PASS production channel real-socket checks; unpatched oversize control42; framework fixture only, no Android Binder claim')


if __name__=='__main__':main()
