// SPDX-License-Identifier: Apache-2.0
#include "luma-task-resize.h"
#include <cassert>
#include <vector>
#include <iostream>

int main() {
    using namespace std::chrono_literals;
    for (double scale : {1.0, 1.25, 1.5, 2.0}) {
        int px = luma::logical_to_pixels(641, scale);
        assert(px >= 641 * scale && px < 641 * scale + 1);
    }
    assert(luma::logical_to_pixels(640, 1.25) == 800);
    assert(luma::logical_to_pixels(641, 1.25) == 802);
    std::mutex mutex;
    std::condition_variable changed;
    std::vector<luma::TaskResize> received;
    bool release = false;
    luma::TaskResizeQueue queue([&](const luma::TaskResize& size) {
        queue.observe_bounds(size);
        std::unique_lock<std::mutex> lock(mutex);
        received.push_back(size);
        changed.notify_all();
        if (size.task == 3) changed.wait(lock, [&] { return release; });
    }, 100ms);
    // One hundred drag updates collapse to the final size. A second task's
    // pending resize is cancelled by Close and must never reach Android.
    for (int width = 500; width < 600; ++width)
        queue.submit({1, 0, 0, width, 700}, true);
    queue.submit({2, 0, 0, 600, 700}, true);
    queue.cancel(2);
    queue.submit({1, 0, 0, 640, 800}, false);
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(changed.wait_for(lock, 2s, [&] { return received.size() == 1; }));
        assert(received[0].task == 1 && received[0].right == 640);
    }
    // An Android relaunch stuck in the bridge cannot hold the Wayland thread.
    queue.submit({3, 0, 0, 640, 800}, false);
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(changed.wait_for(lock, 2s, [&] { return received.size() == 2; }));
    }
    auto start = std::chrono::steady_clock::now();
    queue.submit({4, 0, 0, 800, 900}, false);
    // The worker is still blocked on task3. An old layer which happens to
    // match task4 must not erase its never-delivered initial request.
    queue.observe_bounds({4, 0, 0, 800, 900});
    assert(std::chrono::steady_clock::now() - start < 100ms);
    {
        std::lock_guard<std::mutex> lock(mutex);
        release = true;
    }
    changed.notify_all();
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(changed.wait_for(lock, 2s, [&] { return received.size() == 3; }));
        assert(received[2].task == 4);
        assert(!changed.wait_for(lock, 200ms, [&] { return received.size() != 3; }));
    }
    // A held-still drag eventually updates without needing another event.
    queue.submit({5, 0, 0, 700, 800}, true);
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(!changed.wait_for(lock, 20ms, [&] { return received.size() != 3; }));
        assert(changed.wait_for(lock, 2s, [&] { return received.size() == 4; }));
        assert(received.back().task == 5);
    }
    // An activity may miss a host resize during relaunch. Retry twice without
    // requiring another frame/input event, then sleep rather than spin forever.
    std::vector<luma::TaskResize> retries;
    luma::TaskResizeQueue recovery([&](const luma::TaskResize& size) {
        std::lock_guard<std::mutex> lock(mutex);
        retries.push_back(size); changed.notify_all();
    }, 40ms);
    recovery.submit({8, 0, 0, 900, 600}, false);
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(changed.wait_for(lock, 2s, [&] { return retries.size() == 3; }));
        assert(!changed.wait_for(lock, 150ms, [&] { return retries.size() != 3; }));
    }
    // A stale smaller Android frame is not acknowledgement. Actual bounds
    // within fractional rounding cancel remaining deliveries immediately.
    recovery.submit({9, 10, 20, 900, 600}, false);
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(changed.wait_for(lock, 2s, [&] { return retries.size() == 4; }));
    }
    recovery.observe_bounds({9, 10, 20, 600, 500});
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(changed.wait_for(lock, 2s, [&] { return retries.size() == 5; }));
    }
    recovery.observe_bounds({9, 11, 21, 898, 602});
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(!changed.wait_for(lock, 150ms, [&] { return retries.size() != 5; }));
    }
    // Close after a delivery cancels its scheduled retries; a newer configure
    // also owns the only remaining generation for this task.
    recovery.submit({10, 0, 0, 600, 500}, false);
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(changed.wait_for(lock, 2s, [&] { return retries.size() == 6; }));
    }
    recovery.cancel(10);
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(!changed.wait_for(lock, 150ms, [&] { return retries.size() != 6; }));
    }
    // An in-flight stale generation cannot retry after a newer configure.
    bool entered = false, unblock = false;
    std::vector<int> widths;
    luma::TaskResizeQueue generation([&](const luma::TaskResize& size) {
        std::unique_lock<std::mutex> lock(mutex);
        widths.push_back(size.right); changed.notify_all();
        if (size.right == 600) {
            entered = true; changed.notify_all();
            changed.wait(lock, [&] { return unblock; });
        }
    }, 40ms);
    generation.submit({11, 0, 0, 600, 500}, false);
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(changed.wait_for(lock, 2s, [&] { return entered; }));
    }
    generation.submit({11, 0, 0, 900, 700}, false);
    {
        std::lock_guard<std::mutex> lock(mutex); unblock = true;
    }
    changed.notify_all();
    {
        std::unique_lock<std::mutex> lock(mutex);
        assert(changed.wait_for(lock, 2s, [&] { return widths.size() == 4; }));
        assert(widths == std::vector<int>({600, 900, 900, 900}));
        assert(!changed.wait_for(lock, 150ms, [&] { return widths.size() != 4; }));
    }
    std::cout << "Task resize: scale, coalescing, final delivery, cancellation, blocked bridge, pause, bounded retries and actual-bounds acknowledgement pass\n";
}
