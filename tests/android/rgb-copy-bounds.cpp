#include "luma-rgb-copy-bounds.h"
#include <cassert>
#include <cstring>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include <cstdio>
int main() {
  const size_t page = sysconf(_SC_PAGESIZE);
  auto* mapping = static_cast<unsigned char*>(mmap(nullptr, 3 * page,
      PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
  assert(mapping != MAP_FAILED);
  assert(mprotect(mapping + page, page, PROT_NONE) == 0);
  auto* source = reinterpret_cast<uint32_t*>(mapping);
  auto* destination = reinterpret_cast<uint32_t*>(mapping + 2 * page);
  for (size_t i = 0; i < page / 4; ++i) source[i] = 0x7fa1b2c3;
  std::memset(destination, 0x11, page);
  // Ordinary padded source preserves exactly the requested pixels and leaves
  // destination padding untouched. This is the production conversion body.
  assert(luma::copy_rgb(source, destination, 5, 3, 8, 3, 96, 0, 96, 60));
  for (int i = 0; i < 15; ++i) assert(destination[i] == 0x7fc3b2a1);
  assert(destination[15] == 0x11111111);
  auto reject = [&](uint64_t w, uint64_t h, uint64_t stride, uint64_t physical_h,
                    uint64_t allocation, uint64_t offset, uint64_t capacity, uint64_t output) {
    std::memset(destination, 0x11, page);
    assert(!luma::copy_rgb(source, destination, w, h, stride, physical_h,
                           allocation, offset, capacity, output));
    for (size_t i = 0; i < page / 4; ++i) assert(destination[i] == 0x11111111);
  };
  reject(588, 466, 584, 466, 1088576, 0, 1088576, 1096032); // actual crash tuple
  reject(5, 4, 8, 3, 96, 0, 96, 80); // stale height
  reject(5, 3, 8, 3, 80, 0, 80, 60); // short descriptor
  reject(5, 3, 8, 3, 96, 17, 96, 60); // mapped offset exceeds extent
  reject(5, 3, 8, 3, 96, 0, 95, 60); // fd shorter than claimed mapping
  reject(5, 3, 8, 3, 96, 0, 96, 59); // destination capacity
  reject(0, 3, 8, 3, 96, 0, 96, 60);
  reject(UINT64_MAX, 3, 8, 3, 96, 0, 96, UINT64_MAX);
  reject(5, UINT64_MAX, 8, UINT64_MAX, UINT64_MAX, 0, UINT64_MAX, UINT64_MAX);
  // Exact guarded allocation boundary reproduces the old unchecked over-read
  // in a child. The checked production path rejects it before touching memory.
  const uint64_t stride = page / 8, height = 2, width = stride + 4;
  reject(width, height, stride, height, page, 0, page, page);
  pid_t pid = fork(); assert(pid >= 0);
  if (!pid) {
    volatile uint32_t value = 0;
    for (uint64_t y = 0; y < height; ++y)
      for (uint64_t x = 0; x < width; ++x) value = source[y * stride + x];
    _exit(value ? 0 : 1);
  }
  int status; assert(waitpid(pid, &status, 0) == pid);
  assert(WIFSIGNALED(status) && WTERMSIG(status) == SIGSEGV);
  assert(munmap(mapping, 3 * page) == 0);
  puts("Production RGB bounds/copy PASS; unchecked guarded-page predecessor SIGSEGV as expected");
}
