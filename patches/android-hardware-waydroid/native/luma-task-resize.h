/* SPDX-License-Identifier: Apache-2.0
 * Host configure events must never wait on Android activity relaunches.
 * This worker retains values only, never a window/display/Wayland pointer.
 */
#pragma once
#include <algorithm>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <map>
#include <mutex>
#include <thread>
#include <utility>

namespace luma {
inline int logical_to_pixels(int logical, double scale) {
    return std::max(1, static_cast<int>(std::ceil(logical * (scale > 0 ? scale : 1.0))));
}
struct TaskResize {
    uint32_t task;
    int left, top, right, bottom;
};
class TaskResizeQueue {
    using Clock = std::chrono::steady_clock;
    struct Pending {
        TaskResize size;
        Clock::time_point due;
        uint64_t generation;
        unsigned retries;
        bool interactive;
        bool delivery_started;
    };
    std::function<void(const TaskResize&)> deliver;
    std::chrono::milliseconds pause;
    std::mutex mutex;
    std::condition_variable wake;
    std::map<uint32_t, Pending> pending;
    bool stopping = false;
    uint64_t generation = 0;
    std::thread worker;
    void run() {
        std::unique_lock<std::mutex> lock(mutex);
        while (!stopping) {
            if (pending.empty()) {
                wake.wait(lock, [this] { return stopping || !pending.empty(); });
                continue;
            }
            auto next = std::min_element(pending.begin(), pending.end(),
                [](const auto& a, const auto& b) { return a.second.due < b.second.due; });
            auto deadline = next->second.due;
            if (Clock::now() < deadline) {
                wake.wait_until(lock, deadline);
                continue; // Recompute after a newer configure or cancellation.
            }
            const Pending request = next->second;
            // Keep the expected bounds visible to observations during socket
            // delivery. A new configure, Close, or acknowledgement can replace
            // or erase this generation while the worker waits for Android.
            next->second.due = Clock::time_point::max();
            next->second.delivery_started = true;
            lock.unlock();
            deliver(request.size); // Existing authenticated, bounded socket.
            lock.lock();
            const auto current = pending.find(request.size.task);
            if (current == pending.end() ||
                    current->second.generation != request.generation)
                continue;
            if (!request.interactive && request.retries < 2) {
                current->second.retries = request.retries + 1;
                current->second.due = Clock::now() + pause;
            } else {
                pending.erase(current);
            }
        }
    }
public:
    explicit TaskResizeQueue(std::function<void(const TaskResize&)> callback,
                            std::chrono::milliseconds delay = std::chrono::milliseconds(1500))
        : deliver(std::move(callback)), pause(delay), worker([this] { run(); }) {}
    TaskResizeQueue(const TaskResizeQueue&) = delete;
    TaskResizeQueue& operator=(const TaskResizeQueue&) = delete;
    ~TaskResizeQueue() {
        {
            std::lock_guard<std::mutex> lock(mutex);
            stopping = true;
            pending.clear();
        }
        wake.notify_one();
        worker.join();
    }
    void submit(TaskResize size, bool interactive) {
        {
            std::lock_guard<std::mutex> lock(mutex);
            pending.insert_or_assign(size.task, Pending{size, Clock::now() +
                (interactive ? pause : std::chrono::milliseconds(0)),
                ++generation, 0, interactive, false});
        }
        wake.notify_one();
    }
    // HWC supplies composed task-layer bounds rather than a package-manager
    // acknowledgement. Never accept them before the first request dispatch;
    // two pixels permit fractional-scale rounding after dispatch.
    void observe_bounds(TaskResize actual) {
        {
            std::lock_guard<std::mutex> lock(mutex);
            const auto found = pending.find(actual.task);
            // A matching old/composed layer before first delivery is not an
            // acknowledgement of this newly submitted task resize.
            if (found == pending.end() || !found->second.delivery_started) return;
            const auto& expected = found->second.size;
            if (std::abs(actual.left - expected.left) <= 2 &&
                    std::abs(actual.top - expected.top) <= 2 &&
                    std::abs(actual.right - expected.right) <= 2 &&
                    std::abs(actual.bottom - expected.bottom) <= 2)
                pending.erase(found);
        }
        wake.notify_one();
    }
    void cancel(uint32_t task) {
        {
            std::lock_guard<std::mutex> lock(mutex);
            pending.erase(task);
        }
        wake.notify_one();
    }
};
} // namespace luma
