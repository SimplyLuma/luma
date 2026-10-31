// SPDX-License-Identifier: Apache-2.0
// Compile with the actual patched callbacks inserted at PRODUCTION_CALLBACKS.
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <thread>

struct xdg_toplevel {};
struct wl_shell_surface {};
struct wl_array { size_t size; void* data; };
struct Procs { std::function<void(Procs*)> invalidate; };
struct display {
    Procs* procs;
    int req_width=0, req_height=0, width=1280, height=800;
    bool wm_base=true;
};
struct window {
    static std::shared_ptr<window> acquire_listener(void* token) {
        return *static_cast<std::shared_ptr<window>*>(token);
    }
    struct display* display;
    std::recursive_mutex chromeMutex;
    bool configured=false, has_prairie_titlebar=true, maximized=false;
    std::string taskID="10";
    int content_width=264, content_height=484;
    int restore_task_origin_x=0, restore_task_origin_y=0;
    int task_origin_x=0, task_origin_y=0;
    int chrome_drawn_width=0, chrome_drawn_height=0;
    std::chrono::steady_clock::time_point chrome_drawn_at{};
    void* viewport=nullptr;
    void* xdg_surface=nullptr;
    void request_task_resize(int, int, bool) {}
};
constexpr int LUMA_FRAME_GAP=8;
constexpr uint32_t XDG_TOPLEVEL_STATE_MAXIMIZED=1;
constexpr uint32_t XDG_TOPLEVEL_STATE_RESIZING=3;
namespace prairie {
constexpr int kTitlebarHeight=46;
void redraw_titlebar(window&) {}
void redraw_shadow(window&) {}
}
void wp_viewport_set_destination(void*, int, int) {}
void xdg_surface_set_window_geometry(void*, int, int, int, int) {}
void choose_width_height(display* d, int w, int h) { d->width=w; d->height=h; }
void do_hotplug(display* d) { if (d->procs && d->procs->invalidate) d->procs->invalidate(d->procs); }

// PRODUCTION_CALLBACKS

int main() {
    using namespace std::chrono_literals;
    for (int path=0; path<3; ++path) {
        Procs procs;
        display d{&procs};
        auto owner=std::make_shared<window>(); owner->display=&d;
        owner->has_prairie_titlebar=path==0;
        std::mutex adapter;
        std::atomic<bool> held{false}, entered{false}, rendered{false}, configured{false};
        procs.invalidate=[&](Procs*) {
            entered=true;
            std::scoped_lock adapter_lock(adapter);
        };
        // Match the native capture: present owns the adapter and then needs
        // the window; configure must release the window before invalidating.
        std::thread renderer([&] {
            std::scoped_lock adapter_lock(adapter);
            held=true;
            while (!entered) std::this_thread::yield();
            std::scoped_lock chrome_lock(owner->chromeMutex);
            rendered=true;
        });
        std::thread compositor([&] {
            while (!held) std::this_thread::yield();
            wl_array states{0,nullptr};
            if (path==2) shell_surface_configure(&owner,nullptr,0,900,600);
            else xdg_toplevel_handle_configure(&owner,nullptr,900,600,&states);
            configured=true;
        });
        const auto deadline=std::chrono::steady_clock::now()+1500ms;
        while ((!rendered || !configured) && std::chrono::steady_clock::now()<deadline)
            std::this_thread::sleep_for(1ms);
        if (!rendered || !configured) {
            std::cerr << "configure/present lock inversion reproduced, path=" << path << '\n';
            std::_Exit(42); // Deterministic failure, never strand deadlocked threads.
        }
        compositor.join(); renderer.join();
        if (path==0 && (owner->content_width!=884 || owner->content_height!=546)) return 1;
    }
    std::cout << "PASS: production task, display and legacy configure callbacks avoid adapter/window inversion\n";
}
