/* SPDX-License-Identifier: Apache-2.0
 * Android's authoritative task removal, independent of composition frames.
 * A healthy minimized task stays alive until Android removes its task ID.
 */
#pragma once
#include <cstdint>
#include <mutex>
#include <string>

namespace luma {
template<class Mutex, class Windows, class Ignored, class Cancel>
bool retire_removed_task(uint32_t caller_uid, uint32_t task_id,
                         Mutex& collection_mutex, Windows& windows,
                         Ignored& ignored, Cancel cancel) {
    // Only system_server's TaskStackListener owns this HAL operation.
    if (caller_uid != 1000 || task_id == 0) return false;
    cancel(task_id); // Never hold the collection mutex while locking the queue.
    const auto key = std::to_string(task_id);
    std::scoped_lock lock(collection_mutex);
    const auto found = windows.find(key);
    if (found == windows.end() || !found->second || found->second->taskID != key)
        return false;
    // In-flight listeners retain their strong owner; new admission stops now.
    found->second->retire_listener();
    ignored.insert(key); // Old layers must disappear before this task can reopen.
    windows.erase(found);
    return true;
}
} // namespace luma
