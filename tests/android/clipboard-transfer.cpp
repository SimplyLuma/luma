// SPDX-License-Identifier: Apache-2.0
#include "luma-clipboard-transfer.h"
#include <cassert>
#include <atomic>
#include <dirent.h>
#include <future>
#include <iostream>
#include <sys/mman.h>

using namespace luma::clipboard;
using namespace std::chrono_literals;

struct Result {
    std::promise<std::pair<bool, std::string>> promise;
    std::future<std::pair<bool, std::string>> future = promise.get_future();
    Transfers::Callback callback() {
        return [this](bool ok, std::string text) { promise.set_value({ok, std::move(text)}); };
    }
    std::pair<bool, std::string> get(std::chrono::milliseconds timeout = 3000ms) {
        assert(future.wait_for(timeout) == std::future_status::ready);
        return future.get();
    }
};
int descriptorCount() {
    DIR* dir = opendir("/proc/self/fd");
    assert(dir);
    int count = 0;
    while (readdir(dir)) ++count;
    closedir(dir);
    return count;
}
int body(const std::string& text) {
    int fd = memfd_create("luma-synthetic-clipboard", MFD_CLOEXEC);
    assert(fd >= 0);
    assert(::write(fd, text.data(), text.size()) == static_cast<ssize_t>(text.size()));
    assert(lseek(fd, 0, SEEK_SET) == 0);
    return fd;
}
int main() {
    const int initialDescriptors = descriptorCount();
    struct sigaction original{};
    assert(sigaction(SIGPIPE, nullptr, &original) == 0);
    {
        Transfers transfers;
        std::string text;
        for (unsigned i = 0; i < 20000; ++i) text += "Luma \xf0\x9f\x8c\x99\n";
        // Larger than pipe capacity: simultaneous partial read and write on
        // this same worker must complete with every UTF-8 byte intact.
        int fds[2];
        assert(pipe2(fds, O_CLOEXEC) == 0);
        Result roundTrip;
        transfers.read(fds[0], roundTrip.callback());
        transfers.write(fds[1], std::make_shared<const std::string>(text));
        const auto round = roundTrip.get();
        assert(round.first && round.second == text);

        // A peer that never sends or closes must not hold up an unrelated
        // completed transfer. Replacing it must call its callback once.
        assert(pipe2(fds, O_CLOEXEC) == 0);
        Result stalled, independent;
        transfers.read(fds[0], stalled.callback());
        transfers.read(body("independent"), independent.callback());
        assert(independent.get(500ms) == std::make_pair(true, std::string("independent")));
        assert(!stalled.get(500ms).first);
        close(fds[1]);

        // A receive that is still waiting cannot block an independent send.
        assert(pipe2(fds, O_CLOEXEC) == 0);
        Result waiting;
        transfers.read(fds[0], waiting.callback());
        int destination = memfd_create("luma-synthetic-destination", MFD_CLOEXEC);
        assert(destination >= 0);
        transfers.write(dup(destination), std::make_shared<const std::string>("independent send"));
        const auto sendDeadline = std::chrono::steady_clock::now() + 500ms;
        char sent[16]{};
        ssize_t sentBytes = 0;
        while (std::chrono::steady_clock::now() < sendDeadline) {
            sentBytes = pread(destination, sent, sizeof(sent), 0);
            if (sentBytes == 16) break;
            std::this_thread::sleep_for(1ms);
        }
        assert(sentBytes == 16 && std::string(sent, 16) == "independent send");
        close(destination);
        transfers.cancelRead();
        assert(!waiting.get(500ms).first);
        close(fds[1]);

        Result oversized;
        transfers.read(body(std::string(MaxBytes + 1, 'x')), oversized.callback());
        const auto over = oversized.get();
        assert(!over.first && over.second.empty());
        Result invalid;
        transfers.read(body(std::string("\xed\xa0\x80", 3)), invalid.callback());
        assert(!invalid.get().first);
        for (const auto& bad : {std::string("\xc0\x80", 2), std::string("\xf4\x90\x80\x80", 4),
                                std::string("\x80", 1), std::string("\xe2\x82", 2)}) assert(!validUtf8(bad));

        Result timedOut;
        assert(pipe2(fds, O_CLOEXEC) == 0);
        const auto begin = std::chrono::steady_clock::now();
        transfers.read(fds[0], timedOut.callback());
        assert(!timedOut.get().first);
        const auto elapsed = std::chrono::steady_clock::now() - begin;
        assert(elapsed >= 1900ms && elapsed < 2900ms);
        close(fds[1]);

        // Broken pipe: SIGPIPE is thread-local blocked. A subsequent operation
        // proves the worker and process survived EPIPE.
        assert(pipe2(fds, O_CLOEXEC) == 0);
        close(fds[0]);
        transfers.write(fds[1], std::make_shared<const std::string>("closed peer"));
        Result alive;
        transfers.read(body("alive"), alive.callback());
        assert(alive.get().first);

        // MaxWrites limits accepted descriptors even if all consumers stall.
        std::vector<int> peers;
        for (size_t i = 0; i < MaxWrites + 20; ++i) {
            assert(pipe2(fds, O_CLOEXEC) == 0);
            peers.push_back(fds[0]);
            transfers.write(fds[1], std::make_shared<const std::string>(text));
        }
        assert(descriptorCount() <= initialDescriptors + static_cast<int>(peers.size() + MaxWrites + 3));
        Result cancelled;
        assert(pipe2(fds, O_CLOEXEC) == 0);
        transfers.read(fds[0], cancelled.callback());
        const auto stop = std::chrono::steady_clock::now();
        transfers.shutdown();
        assert(std::chrono::steady_clock::now() - stop < 500ms);
        assert(!cancelled.get(500ms).first);
        close(fds[1]);
        for (int peer : peers) close(peer);
        Result rejected;
        transfers.read(body("after shutdown"), rejected.callback());
        assert(!rejected.get().first);
    }
    struct sigaction after{};
    assert(sigaction(SIGPIPE, nullptr, &after) == 0);
    assert(after.sa_handler == original.sa_handler);
    {
        Transfers concurrent;
        std::atomic<unsigned> started{0}, callbacks{0};
        std::vector<std::thread> producers;
        for (unsigned p = 0; p < 4; ++p) {
            producers.emplace_back([&] {
                ++started;
                for (unsigned i = 0; i < 200; ++i)
                    concurrent.read(body("concurrent synthetic selection"),
                        [&](bool, std::string) { ++callbacks; });
            });
        }
        while (started.load() != 4) std::this_thread::yield();
        concurrent.shutdown();
        for (auto& producer : producers) producer.join();
        assert(callbacks.load() == 800);
    }
    assert(descriptorCount() == initialDescriptors);

    Cache cache;
    auto old = cache.beginRead();
    cache.set("new Android selection");
    cache.complete(old, true, "stale host selection");
    assert(cache.get() == "new Android selection");
    old = cache.beginRead();
    const auto current = cache.beginRead();
    cache.complete(old, true, "obsolete");
    cache.complete(current, true, "current");
    assert(cache.get() == "current");
    const auto failure = cache.beginRead();
    cache.complete(failure, false, "must not expose partial content");
    assert(cache.get().empty());
    std::cout << "PASS: real pipe round trip, partial I/O, cancellation, UTF-8/size limits, deadline, EPIPE, shutdown, FD ownership and cache generations\n";
}
