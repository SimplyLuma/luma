/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-window-callbacks.h"
#include <atomic>
#include <cassert>
#include <cstdio>
#include <thread>
#include <vector>
struct Window {
    std::atomic<int>& destroyed;
    explicit Window(std::atomic<int>& d):destroyed(d){}
    ~Window(){destroyed++;}
};
int main() {
    luma::WindowCallbacks<Window> callbacks;
    std::atomic<int> destroyed{0};
    auto owner=std::make_shared<Window>(destroyed);
    void* first=callbacks.attach(owner);assert(first);
    auto in_flight=callbacks.acquire(first);assert(in_flight==owner);
    assert(callbacks.retire(first));assert(!callbacks.retire(first));
    assert(!callbacks.acquire(first));
    owner.reset();assert(destroyed==0);in_flight.reset();assert(destroyed==1);
    auto next=std::make_shared<Window>(destroyed);void* second=callbacks.attach(next);
    assert(second && second!=first && !callbacks.acquire(first));
    assert(!callbacks.acquire(nullptr));assert(!callbacks.acquire(reinterpret_cast<void*>(UINTPTR_MAX)));
    std::atomic<bool> go{false};std::thread callback([&]{while(!go)std::this_thread::yield();for(int i=0;i<10000;i++){auto admitted=callbacks.acquire(second);if(admitted)assert(admitted==next);}});
    go=true;assert(callbacks.retire(second));callback.join();assert(!callbacks.acquire(second));next.reset();assert(destroyed==2);
    std::vector<std::shared_ptr<Window>> active;std::vector<void*> tokens;
    for(int i=0;i<4096;i++){active.push_back(std::make_shared<Window>(destroyed));tokens.push_back(callbacks.attach(active.back()));assert(tokens.back());}
    auto refused=std::make_shared<Window>(destroyed);assert(!callbacks.attach(refused));
    for(void*t:tokens){assert(callbacks.retire(t));}
    active.clear();refused.reset();assert(destroyed==4099);
    struct Reentrant {luma::WindowCallbacks<Reentrant>* registry;void* token{};~Reentrant(){registry->retire(token);}};
    luma::WindowCallbacks<Reentrant> reentrant;auto target=std::make_shared<Reentrant>();target->registry=&reentrant;target->token=reentrant.attach(target);auto held=reentrant.acquire(target->token);target.reset();held.reset();
    puts("PASS: retired/unknown/ABA/capacity refusal; in-flight owner delays teardown; concurrent retirement; destructor registry reentry");
}
