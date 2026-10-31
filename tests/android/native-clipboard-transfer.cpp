// SPDX-License-Identifier: Apache-2.0
// Explicit Wayland/HIDL dependency fixtures around the complete production
// clipboard translation unit. Pipes and the transfer worker are real. This is
// not a Wayland-server or Android HwBinder IPC qualification.
#include "luma-clipboard-transfer.h"
#include "luma-window-callbacks.h"
#include <atomic>
#include <cassert>
#include <future>
#include <iostream>
#include <map>

struct LumaClipboardState;
struct wl_display {};
struct wl_data_device {};
struct wl_data_device_manager {};
struct wl_surface {};
using wl_fixed_t = int32_t;
struct wl_data_source;
struct wl_data_source_listener {
    void (*target)(void*,wl_data_source*,const char*);
    void (*send)(void*,wl_data_source*,const char*,int);
    void (*cancelled)(void*,wl_data_source*);
    void (*dnd_drop_performed)(void*,wl_data_source*);
    void (*dnd_finished)(void*,wl_data_source*);
    void (*action)(void*,wl_data_source*,uint32_t);
};
struct wl_data_source { const wl_data_source_listener* listener{}; void* data{}; };
struct wl_data_offer { wl_data_source* source{}; int held = -1; };
struct wl_data_offer_listener {
    void (*offer)(void*,wl_data_offer*,const char*);
    void (*source_actions)(void*,wl_data_offer*,uint32_t);
    void (*action)(void*,wl_data_offer*,uint32_t);
};
struct wl_data_device_listener {
    void (*data_offer)(void*,wl_data_device*,wl_data_offer*);
    void (*enter)(void*,wl_data_device*,uint32_t,wl_surface*,wl_fixed_t,wl_fixed_t,wl_data_offer*);
    void (*leave)(void*,wl_data_device*);
    void (*motion)(void*,wl_data_device*,uint32_t,wl_fixed_t,wl_fixed_t);
    void (*drop)(void*,wl_data_device*);
    void (*selection)(void*,wl_data_device*,wl_data_offer*);
};
struct display {
    wl_display* display{};
    wl_data_device* data_device{};
    wl_data_device_manager* data_device_manager{};
    std::atomic<uint32_t> keyboard_enter_serial{9};
    std::shared_ptr<LumaClipboardState> clipboard_state;
    std::vector<std::string> clipboard_offer_mime_types;
};
int sourceCount = 0, flushes = 0;
wl_data_source* selection = nullptr;
wl_data_source* wl_data_device_manager_create_data_source(wl_data_device_manager*) { ++sourceCount; return new wl_data_source; }
void wl_data_source_add_listener(wl_data_source* s,const wl_data_source_listener* l,void* p) { s->listener=l; s->data=p; }
void wl_data_source_offer(wl_data_source*,const char*) {}
void* wl_data_source_get_user_data(wl_data_source* s) { return s->data; }
void wl_data_source_destroy(wl_data_source* s) { --sourceCount; delete s; }
void wl_data_device_set_selection(wl_data_device*,wl_data_source* s,uint32_t serial) { assert(serial==9); selection=s; }
int wl_display_flush(wl_display*) { return ++flushes; }
void wl_data_offer_destroy(wl_data_offer*) {}
void wl_data_offer_receive(wl_data_offer* o,const char*,int fd) {
    if (o->source) o->source->listener->send(o->source->data,o->source,"text/plain;charset=utf-8",dup(fd));
    else o->held=dup(fd); // Simulated stalled foreign client.
}
void wl_data_offer_add_listener(wl_data_offer*,const wl_data_offer_listener*,void*) {}
void wl_data_device_add_listener(wl_data_device*,const wl_data_device_listener*,void*) {}

constexpr unsigned AID_SYSTEM=1000;
namespace android::hardware {
struct IPCThreadState {
    static inline unsigned caller=1000;
    static IPCThreadState* self() { static IPCThreadState value; return &value; }
    unsigned getCallingUid() { return caller; }
};
}
template<class T> struct Return {};
Return<void> Void() { return {}; }
using hidl_string=std::string;
namespace vendor::waydroid::clipboard::implementation {
struct WaydroidClipboard {
    using getClipboardData_cb=std::function<void(const std::string&)>;
    display* mDisplay;
    std::shared_ptr<LumaClipboardState> mState;
    WaydroidClipboard(display*);
    Return<void> sendClipboardData(const hidl_string&);
    Return<void> getClipboardData(getClipboardData_cb);
};
}

// PRODUCTION_CLIPBOARD

std::string get(vendor::waydroid::clipboard::implementation::WaydroidClipboard& hal) {
    std::string result;
    int callbacks=0;
    hal.getClipboardData([&](const std::string& text) { ++callbacks; result=text; });
    assert(callbacks==1);
    return result;
}
int main() {
    using namespace std::chrono_literals;
    display state;
    wl_display connection;
    wl_data_device device;
    state.display=&connection;
    state.data_device=&device;
    vendor::waydroid::clipboard::implementation::WaydroidClipboard hal(&state);
    std::string original;
    for (unsigned i=0;i<20000;++i) original += "synthetic \xf0\x9f\x8c\x99\n";
    hal.sendClipboardData(original);
    auto* source=selection;
    void* sourceToken=source->data;
    assert(get(hal)==original && sourceCount==1);
    // Exact production sender and selection callbacks feed the same real pipe.
    wl_data_offer offer{source};
    data_offer_handle_offer(&state,&offer,"text/plain;charset=utf-8");
    const auto before=std::chrono::steady_clock::now();
    data_device_handle_selection(&state,&device,&offer);
    assert(std::chrono::steady_clock::now()-before<100ms);
    assert(get(hal)==original);
    for (unsigned uid : {0u,1001u,10131u,99000u}) {
        android::hardware::IPCThreadState::caller=uid;
        assert(get(hal).empty());
        hal.sendClipboardData("forbidden");
        android::hardware::IPCThreadState::caller=1000;
        assert(get(hal)==original && sourceCount==1);
    }
    // Stalled peer: selection dispatch returns immediately, a newer local copy
    // cancels its worker receive, and an obsolete completion cannot overwrite it.
    wl_data_offer stalled;
    data_offer_handle_offer(&state,&stalled,"text/plain");
    const auto stalledStart=std::chrono::steady_clock::now();
    data_device_handle_selection(&state,&device,&stalled);
    assert(std::chrono::steady_clock::now()-stalledStart<100ms);
    hal.sendClipboardData("new local selection");
    assert(get(hal)=="new local selection");
    close(stalled.held);
    // A source's immutable data remains valid while new HwBinder requests
    // arrive. Admission owns a snapshot across source retirement.
    int fds[2];
    assert(pipe2(fds,O_CLOEXEC)==0);
    source->listener->send(source->data,source,"text/plain",fds[1]);
    std::thread setter([&] { for (unsigned i=0;i<40;++i) hal.sendClipboardData("rapid synthetic copy " + std::to_string(i)); });
    std::string received;
    char buffer[8192];
    for (;;) { const auto n=read(fds[0],buffer,sizeof(buffer)); if(n<=0)break; received.append(buffer,n); }
    close(fds[0]);
    setter.join();
    assert(received==original && sourceCount<=8);
    assert(get(hal)=="rapid synthetic copy 39");
    // A queued stale callback keeps only the retired integer token, never a
    // payload pointer. It must close its FD and ignore cancellation safely.
    assert(pipe2(fds,O_CLOEXEC)==0);
    data_source_handle_send(sourceToken,nullptr,"text/plain",fds[1]);
    assert(read(fds[0],buffer,sizeof(buffer))==0);
    close(fds[0]);
    data_source_handle_cancelled(sourceToken,nullptr);
    // Duplicate and unrecognized MIME declarations do not grow metadata.
    for (unsigned i=0;i<10000;++i) {
        data_offer_handle_offer(&state,nullptr,"text/plain");
        data_offer_handle_offer(&state,nullptr,"application/ignored");
    }
    assert(state.clipboard_offer_mime_types.size()==1);
    data_device_handle_selection(&state,&device,nullptr);
    assert(get(hal).empty());
    luma_clipboard_shutdown(&state);
    assert(sourceCount==0);
    // Simulate the display being gone: neither late HAL call may dereference it.
    hal.mDisplay=nullptr;
    hal.sendClipboardData("after teardown");
    assert(get(hal).empty());
    std::cout << "PASS: complete production clipboard callbacks/HAL with dependency fixtures, real asynchronous pipes, immutable snapshots, denied callers and teardown\n";
}
