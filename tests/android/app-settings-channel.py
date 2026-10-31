#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the production settings admission/forwarding body with real Unix sockets.

Only Binder is substituted. Private user namespaces supply actual kernel peer
UIDs; no host identity is changed. This is not Android Binder qualification.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

PREFIX = r'''
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#include <cassert>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>
#include "luma-app-settings-channel.h"
#ifndef TEMP_FAILURE_RETRY
#define TEMP_FAILURE_RETRY(x) (x)
#endif
namespace android {
struct String16 { std::string value; explicit String16(const char* s):value(s){} };
struct Parcel {
 void writeInterfaceToken(const String16& s){assert(s.value=="org.projectluma.android.IAppSettings");}
 void writeString16(const String16& s){assert(s.value=="com.example.game");}
};
struct IBinder {static constexpr int FLAG_ONEWAY=1;};
struct Host {int calls=0; void transact(int code,const Parcel&,void*,int flags){assert(code==1&&flags==1);++calls;}} host;
struct Manager {int calls=0; Host* checkService(const String16& s){assert(s.value=="lumaappsettings");++calls;return &host;}} manager;
Manager* defaultServiceManager(){return &manager;}
}
'''
TEST = r'''
int main(){
 using namespace luma::app_settings;
 using Clock=std::chrono::steady_clock;
 Request good{};good.magic=magic;good.version=version;strcpy(good.package,"com.example.game");
 for(int scenario=0;scenario<9;++scenario){
  int fds[2];assert(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,fds)==0);
  Request r=good;
  if(scenario==1)r.magic=0;
  if(scenario==2)r.version=99;
  if(scenario==3)r.reserved=1;
  if(scenario==4)memset(r.package,'x',sizeof(r.package));
  std::vector<char> bytes(sizeof(r));memcpy(bytes.data(),&r,sizeof(r));
  if(scenario==5)bytes.push_back('x');
  if(scenario==6)bytes.pop_back();
  if(scenario==8){close(fds[1]);fds[1]=-1;}
  if(scenario<7)assert(send(fds[1],bytes.data(),bytes.size(),MSG_NOSIGNAL)==static_cast<ssize_t>(bytes.size()));
  auto start=Clock::now();handle(fds[0]);
  auto elapsed=std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now()-start).count();
  assert(elapsed<1500);
  if(scenario==7&&getuid()==1000)assert(elapsed>=200);
  const int expected=getuid()==1000?1:0;
  assert(android::manager.calls==expected&&android::host.calls==expected);
  if(fds[1]>=0)close(fds[1]);
 }
 assert(getuid()==1000||getuid()==10000);
 std::cout<<"PASS actual SO_PEERCRED UID"<<getuid()<<"; valid/malformed/oversize/short/stalled/closed settings requests; Binder fixture\n";
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('hardware_source', type=Path)
    args = parser.parse_args()
    source = (args.hardware_source/'interfaces/task/1.0/default/service.cpp').read_text()
    source = source[source.index('void serveAppSettingsRequests()'):]
    start = source.index('        ucred credentials{};')
    end = source.index('\n    }\n}', start)
    body = source[start:end]
    repo = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='luma-settings-channel-') as directory:
        unit = Path(directory)/'channel.cpp'
        binary = Path(directory)/'channel'
        def compile_body(value):
            unit.write_text(PREFIX+'\nvoid handle(int client){using namespace luma::app_settings; do {\n'+value+'\n} while(false);}\n'+TEST)
            subprocess.run(['c++','-std=c++17','-O1','-Wall','-Wextra','-Werror',
                '-I',str(repo/'patches/android-hardware-waydroid/native'),str(unit),'-o',str(binary)],check=True)
        def execute(uid):
            return subprocess.run(['unshare','--user','--map-user='+str(uid),
                '--map-group='+str(uid),str(binary)],timeout=10)
        compile_body(body)
        for uid in (1000,10000):
            assert execute(uid).returncode == 0
        # Both controls must fail: otherwise the checks never reached admission
        # or they accepted the truncated prefix of a larger packet.
        for old,new,uid in [('MSG_TRUNC','0',1000),
                ('credentials.uid == 1000','true',10000)]:
            assert body.count(old)==1
            compile_body(body.replace(old,new))
            assert execute(uid).returncode != 0, ('negative control passed',old)
        print('PASS production settings socket body; both deliberate negative controls failed')


if __name__=='__main__':
    main()
