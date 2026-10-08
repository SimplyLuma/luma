/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <cstdint>
#include <limits>
#include <map>
#include <memory>
#include <mutex>

namespace luma {
// Wayland listener userdata is an opaque generation, never an object pointer.
// Weak entries are retired before dropping collection ownership. Admission
// returns a strong owner after releasing this registry's mutex, so neither
// window locks nor protocol calls run while the registry mutex is held.
template<class T> class WindowCallbacks {
    std::mutex mutex;
    std::map<uintptr_t, std::weak_ptr<T>> active;
    uintptr_t next = 1;
public:
    void* attach(const std::shared_ptr<T>& owner) {
        std::lock_guard<std::mutex> lock(mutex);
        if (!owner || active.size() >= 4096 || next == std::numeric_limits<uintptr_t>::max())
            return nullptr;
        const uintptr_t token = next++;
        active.emplace(token, owner);
        return reinterpret_cast<void*>(token);
    }
    std::shared_ptr<T> acquire(void* data) {
        std::lock_guard<std::mutex> lock(mutex);
        const auto found = active.find(reinterpret_cast<uintptr_t>(data));
        return found == active.end() ? nullptr : found->second.lock();
    }
    bool retire(void* data) {
        std::lock_guard<std::mutex> lock(mutex);
        return active.erase(reinterpret_cast<uintptr_t>(data)) != 0;
    }
};
}
