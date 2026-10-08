// SPDX-License-Identifier: Apache-2.0
#include "luma-task-lifetime.h"
#include "luma-window-callbacks.h"
#include <cassert>
#include <atomic>
#include <map>
#include <memory>
#include <set>
#include <thread>

struct Window;
luma::WindowCallbacks<Window> registry;
std::atomic<unsigned> destroyed{0};
struct Window {
    std::string taskID;
    void* token{};
    bool minimized = true;
    bool retire_listener() {return registry.retire(token);}
    ~Window() {++destroyed; registry.retire(token);}
};
std::shared_ptr<Window> make(const std::string& id) {
    auto window = std::make_shared<Window>();window->taskID=id;
    window->token=registry.attach(window);return window;
}
int main() {
    std::recursive_mutex mutex;
    std::map<std::string,std::shared_ptr<Window>> windows;
    std::set<std::string> ignored;
    std::atomic<unsigned> cancelled{0};
    auto cancel = [&](uint32_t id) {assert(id==32 || id==99);++cancelled;};
    windows.emplace("32",make("32"));
    windows.emplace("33",make("33")); // Healthy minimized task must survive.
    const auto token=windows.at("32")->token;
    auto in_flight=registry.acquire(token);assert(in_flight);
    for (const unsigned uid : {0u,1001u,10000u,4294967295u}) {
        assert(!luma::retire_removed_task(uid,32,mutex,windows,ignored,cancel));
        assert(windows.size()==2 && ignored.empty() && cancelled==0);
        assert(registry.acquire(token));
    }
    assert(!luma::retire_removed_task(1000,0,mutex,windows,ignored,cancel));
    assert(cancelled==0);
    assert(!luma::retire_removed_task(1000,99,mutex,windows,ignored,cancel));
    assert(cancelled==1 && windows.size()==2 && ignored.empty());
    assert(luma::retire_removed_task(1000,32,mutex,windows,ignored,cancel));
    assert(cancelled==2 && windows.count("32")==0 && ignored.count("32")==1);
    assert(!registry.acquire(token)); // No new admission after collection erase.
    assert(destroyed==0); // Already admitted callback keeps its object alive.
    assert(in_flight->taskID=="32");
    in_flight.reset();assert(destroyed==1);
    assert(windows.at("33")->minimized && registry.acquire(windows.at("33")->token));
    assert(!luma::retire_removed_task(1000,32,mutex,windows,ignored,cancel));
    assert(destroyed==1 && windows.size()==1);
    windows.clear();assert(destroyed==2);
}
