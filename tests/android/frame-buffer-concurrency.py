#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the actual patched redraw body with native mmap and proxy lifetime.

Wayland protocol drawing calls are private deterministic transport stand-ins.
The production allocation/replacement body is copied verbatim from the actual
series result. A missing lock must fail the paired simultaneous-paint control.
Installed compositor clean-boot behavior remains a separate mandatory gate.
"""
import argparse,pathlib,subprocess,tempfile,hashlib
P=argparse.ArgumentParser();P.add_argument('prepared_hwc',type=pathlib.Path);a=P.parse_args()
source=(a.prepared_hwc/'prairie-titlebar.cpp').read_text();begin=source.index('void redraw_titlebar(window& window) {');body=source[begin:source.index('\n}\n',begin)+2]
assert body.count('std::scoped_lock chrome_lock(window.chromeMutex);')==1
prefix=r'''
#include <mutex>
#include <thread>
#include <atomic>
#include <vector>
#include <cstdint>
#include <cstdlib>
#include <algorithm>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <linux/memfd.h>
#include <unistd.h>
#include <cstdio>
struct wl_buffer { std::atomic<bool> destroyed{false}; };
struct wl_shm_pool {};
struct wl_region {};
struct display { void* shm{};void* compositor{}; };
struct window { mutable std::recursive_mutex chromeMutex; bool has_prairie_titlebar{true};int content_width{342},content_height{626};wl_buffer* titlebar_buffer{};void* titlebar_data{};size_t titlebar_data_size{};struct display* display{};const char* surface_treatment{};bool dark_appearance{};void* titlebar_surface{}; };
struct Rect{int x,y,width,height;};
struct Geometry{Rect outer,content;};
Geometry frame(window& w){std::scoped_lock l(w.chromeMutex);return {{0,0,w.content_width+16,w.content_height+54},{8,46,w.content_width,w.content_height}};}
constexpr int LUMA_FRAME_ISLAND_RADIUS=12,WL_SHM_FORMAT_ARGB8888=0;
enum class SurfaceTreatment{Light,Dark};
using LumaFrameMaterial=int;
SurfaceTreatment read_surface_treatment(){return SurfaceTreatment::Dark;}
const char* treatment_name(SurfaceTreatment){return "dark";}
LumaFrameMaterial frame_material(window&){return 0;}
uint32_t luma_frame_pixel_material(Geometry,int,int,int){return 0xff123456;}
int control_left(window&,int){return 0;}
void draw_control(window&,int,int){}
void draw_back(window&){} void draw_app_icon(window&){} void draw_title(window&){}
std::mutex poolMutex;std::vector<wl_buffer*> proxies;
void wl_buffer_destroy(wl_buffer* b){if(b->destroyed.exchange(true)){std::fprintf(stderr,"proxy destroyed twice\n");std::_Exit(91);}std::this_thread::sleep_for(std::chrono::microseconds(150));}
wl_shm_pool* wl_shm_create_pool(void*,int,size_t){return new wl_shm_pool;}
wl_buffer* wl_shm_pool_create_buffer(wl_shm_pool*,int,int,int,size_t,int){auto*b=new wl_buffer;std::scoped_lock l(poolMutex);proxies.push_back(b);return b;}
void wl_shm_pool_destroy(wl_shm_pool*p){delete p;}
wl_region* wl_compositor_create_region(void*){return new wl_region;}
void wl_region_add(wl_region*,int,int,int,int){} void wl_region_subtract(wl_region*,int,int,int,int){}
void wl_surface_set_input_region(void*,wl_region*){}void wl_region_destroy(wl_region*p){delete p;}
void wl_surface_attach(void*,wl_buffer*,int,int){}void wl_surface_damage(void*,int,int,int,int){}void wl_surface_commit(void*){}
'''
suffix=r'''
int main(){display d;window w;w.display=&d;redraw_titlebar(w);std::atomic<int> ready{0};auto paint=[&]{ready++;while(ready.load()!=2)std::this_thread::yield();for(int i=0;i<120;i++)redraw_titlebar(w);};std::thread a(paint),b(paint);a.join();b.join();wl_buffer_destroy(w.titlebar_buffer);munmap(w.titlebar_data,w.titlebar_data_size);for(auto*b:proxies){if(!b->destroyed.load())return 92;delete b;}if(proxies.size()!=241)return 93;puts("Actual redraw body: 241 proxy allocations each destroyed once; concurrent native mapped writes completed");}
'''
with tempfile.TemporaryDirectory(prefix='luma-frame-native-lock-') as temp:
 root=pathlib.Path(temp)
 for name,render in [('positive',body),('unlocked-negative',body.replace('    std::scoped_lock chrome_lock(window.chromeMutex);\n','',1))]:
  cpp=root/(name+'.cpp');cpp.write_text(prefix+render+suffix);exe=root/name
  subprocess.run(['c++','-std=c++17','-O1','-pthread','-Wall','-Wextra','-Werror',str(cpp),'-o',str(exe)],check=True)
  result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=20)
  print(name,result.returncode,result.stdout.strip(),result.stderr.strip(),flush=True)
  if name=='positive' and result.returncode!=0:raise AssertionError('Actual guarded redraw failed')
  if name=='unlocked-negative' and result.returncode==0:raise AssertionError('Unlocked lifetime control did not go RED')
 print('Exact production redraw SHA256',hashlib.sha256(body.encode()).hexdigest())
