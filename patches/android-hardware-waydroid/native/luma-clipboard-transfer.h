// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Luma contributors
#pragma once

#include <algorithm>
#include <chrono>
#include <cerrno>
#include <condition_variable>
#include <cstdint>
#include <fcntl.h>
#include <functional>
#include <memory>
#include <mutex>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <string>
#include <sys/eventfd.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace luma::clipboard {

// Bound both the peer's allocation and its time holding descriptors. This is
// plain UTF-8 text; rich clipboard content needs a versioned descriptor API.
constexpr size_t MaxBytes = 1024 * 1024;
constexpr size_t MaxWrites = 16;
constexpr auto TransferTimeout = std::chrono::seconds(2);

inline bool validUtf8(const std::string& text) {
    size_t i = 0;
    while (i < text.size()) {
        const auto c = static_cast<unsigned char>(text[i++]);
        if (c < 0x80) continue;
        unsigned count;
        uint32_t value, minimum;
        if (c >= 0xc2 && c <= 0xdf) { count = 1; value = c & 0x1f; minimum = 0x80; }
        else if (c >= 0xe0 && c <= 0xef) { count = 2; value = c & 0x0f; minimum = 0x800; }
        else if (c >= 0xf0 && c <= 0xf4) { count = 3; value = c & 7; minimum = 0x10000; }
        else return false;
        if (count > text.size() - i) return false;
        while (count--) {
            const auto next = static_cast<unsigned char>(text[i++]);
            if ((next & 0xc0) != 0x80) return false;
            value = (value << 6) | (next & 0x3f);
        }
        if (value < minimum || value > 0x10ffff || (value >= 0xd800 && value <= 0xdfff)) return false;
    }
    return true;
}

class Cache {
public:
    uint64_t beginRead() {
        std::lock_guard<std::mutex> lock(mutex_);
        text_.clear();
        receiving_ = true;
        return ++generation_;
    }
    void complete(uint64_t generation, bool ok, std::string text) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (generation != generation_) return;
        text_ = ok ? std::move(text) : std::string();
        receiving_ = false;
        ready_.notify_all();
    }
    void set(std::string text) {
        std::lock_guard<std::mutex> lock(mutex_);
        ++generation_;
        receiving_ = false;
        text_ = std::move(text);
        ready_.notify_all();
    }
    std::string get() {
        std::unique_lock<std::mutex> lock(mutex_);
        // Only a HwBinder caller waits, never the Wayland dispatch thread.
        ready_.wait_for(lock, TransferTimeout, [&] { return !receiving_; });
        return text_;
    }
private:
    std::mutex mutex_;
    std::condition_variable ready_;
    std::string text_;
    uint64_t generation_ = 0;
    bool receiving_ = false;
};

// Owns every submitted FD, including rejected submissions. One poll worker can
// receive and send on the same pipe without a recursive Wayland dispatch or a
// pipe-capacity deadlock. With no work it sleeps indefinitely on eventfd.
class Transfers {
public:
    using Callback = std::function<void(bool, std::string)>;
    Transfers() : wake_(eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK)) {
        if (wake_ >= 0) worker_ = std::thread([this] { run(); });
    }
    ~Transfers() { shutdown(); }
    Transfers(const Transfers&) = delete;
    Transfers& operator=(const Transfers&) = delete;

    void read(int fd, Callback callback) { submit(fd, nullptr, std::move(callback)); }
    void write(int fd, std::shared_ptr<const std::string> text) {
        if (!text || text->size() > MaxBytes || !validUtf8(*text)) { close(fd); return; }
        submit(fd, std::move(text), {});
    }
    void cancelRead() {
        std::lock_guard<std::mutex> lock(mutex_);
        ++readGeneration_;
        notifyLocked();
    }
    void shutdown() {
        // Callers serialize shutdown. Submissions may race it safely.
        {
            std::lock_guard<std::mutex> lock(mutex_);
            stopping_ = true;
            notifyLocked();
        }
        if (worker_.joinable()) worker_.join();
        std::lock_guard<std::mutex> lock(mutex_);
        if (wake_ >= 0) { close(wake_); wake_ = -1; }
    }
private:
    using Clock = std::chrono::steady_clock;
    struct Job {
        int fd;
        std::shared_ptr<const std::string> text;
        Callback callback;
        std::string received;
        size_t offset = 0;
        uint64_t generation;
        Clock::time_point deadline;
    };
    std::mutex mutex_;
    std::vector<Job> pending_;
    std::thread worker_;
    int wake_;
    bool stopping_ = false;
    size_t writes_ = 0;
    uint64_t readGeneration_ = 0;

    void notifyLocked() {
        if (wake_ < 0) return;
        const uint64_t one = 1;
        ssize_t n;
        do { n = ::write(wake_, &one, sizeof(one)); } while (n < 0 && errno == EINTR);
        // EAGAIN means an existing wake is already pending.
    }
    void submit(int fd, std::shared_ptr<const std::string> text, Callback callback) {
        bool accepted = false;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            const int flags = fcntl(fd, F_GETFL);
            if (!stopping_ && wake_ >= 0 && flags >= 0 &&
                fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0 &&
                (text ? writes_ < MaxWrites : pending_.size() < MaxWrites + 1)) {
                if (text) ++writes_;
                else ++readGeneration_;
                pending_.push_back({fd, std::move(text), std::move(callback), {}, 0,
                                    readGeneration_, Clock::now() + TransferTimeout});
                accepted = true;
                notifyLocked();
            }
        }
        if (!accepted) { close(fd); if (callback) callback(false, {}); }
    }
    void finish(Job& job, bool ok) {
        close(job.fd);
        if (job.text) {
            std::lock_guard<std::mutex> lock(mutex_);
            --writes_;
        } else if (job.callback) {
            job.callback(ok, ok ? std::move(job.received) : std::string());
        }
    }
    void run() {
        // A peer closing its read end must not terminate SurfaceFlinger. Keep
        // SIGPIPE blocked only in this dedicated worker, not process-wide.
        sigset_t signals;
        sigemptyset(&signals);
        sigaddset(&signals, SIGPIPE);
        pthread_sigmask(SIG_BLOCK, &signals, nullptr);
        std::vector<Job> jobs;
        for (;;) {
            uint64_t generation;
            bool stopping;
            {
                std::lock_guard<std::mutex> lock(mutex_);
                for (auto& job : pending_) jobs.push_back(std::move(job));
                pending_.clear();
                generation = readGeneration_;
                stopping = stopping_;
            }
            const auto now = Clock::now();
            for (auto it = jobs.begin(); it != jobs.end();) {
                if (stopping || now >= it->deadline || (!it->text && it->generation != generation)) {
                    finish(*it, false);
                    it = jobs.erase(it);
                } else ++it;
            }
            if (stopping) break;
            std::vector<pollfd> fds{{wake_, POLLIN, 0}};
            int timeout = -1;
            for (const auto& job : jobs) {
                fds.push_back({job.fd, static_cast<short>(job.text ? POLLOUT : POLLIN), 0});
                const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(job.deadline - now).count();
                const int remaining = static_cast<int>(std::max<int64_t>(1, ms));
                timeout = timeout < 0 ? remaining : std::min(timeout, remaining);
            }
            const int result = poll(fds.data(), fds.size(), timeout);
            if (result < 0 && errno == EINTR) continue;
            if (result < 0) {
                for (auto& job : jobs) finish(job, false);
                jobs.clear();
                continue;
            }
            if (fds[0].revents) {
                uint64_t value;
                while (::read(wake_, &value, sizeof(value)) < 0 && errno == EINTR) {}
                // Reconcile queued cancellation/shutdown before any more I/O.
                continue;
            }
            for (size_t i = jobs.size(); i > 0; --i) {
                auto& job = jobs[i - 1];
                if (!fds[i].revents) continue;
                bool done = false, ok = false;
                // Bound work per FD so a ready writer cannot starve another
                // transfer, cancellation or its own deadline.
                for (unsigned chunk = 0; chunk < 16 && !done; ++chunk) {
                    if (job.text) {
                        const auto remaining = job.text->size() - job.offset;
                        if (remaining == 0) { done = ok = true; break; }
                        const ssize_t n = ::write(job.fd, job.text->data() + job.offset,
                                                  std::min<size_t>(16384, remaining));
                        if (n > 0) job.offset += static_cast<size_t>(n);
                        else if (n < 0 && errno == EINTR) continue;
                        else if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
                        else done = true;
                        if (job.offset == job.text->size()) done = ok = true;
                    } else {
                        char buffer[16384];
                        const ssize_t n = ::read(job.fd, buffer, sizeof(buffer));
                        if (n > 0) {
                            if (static_cast<size_t>(n) > MaxBytes - job.received.size()) done = true;
                            else job.received.append(buffer, static_cast<size_t>(n));
                        } else if (n == 0) { done = true; ok = validUtf8(job.received); }
                        else if (errno == EINTR) continue;
                        else if (errno == EAGAIN || errno == EWOULDBLOCK) break;
                        else done = true;
                    }
                }
                if (done) { finish(job, ok); jobs.erase(jobs.begin() + i - 1); }
            }
        }
    }
};

} // namespace luma::clipboard
