// SPDX-License-Identifier: BSD-3-Clause
/* Minimal BufferAllocator ABI used by QREL's libQSEEComAPI. */

#include <stddef.h>

extern int luma_qcomtee_allocate(size_t size);

void luma_buffer_allocator_ctor(void *self)
    __asm__("_ZN15BufferAllocatorC1Ev");
int luma_buffer_allocator_alloc(void *self, const void *heap_name, size_t size,
                                unsigned int flags, size_t alignment)
    __asm__("_ZN15BufferAllocator5AllocERKNSt3__112basic_stringIcNS0_11char_traitsIcEENS0_9allocatorIcEEEEmjm");
int luma_buffer_allocator_sync_start(void *self, unsigned int fd, int sync_type,
                                     const void *legacy_sync,
                                     const void *dmabuf_sync)
    __asm__("_ZN15BufferAllocator12CpuSyncStartEj8SyncTypeRKNSt3__18functionIFiiiPvEEES3_");
int luma_buffer_allocator_sync_end(void *self, unsigned int fd, int sync_type,
                                   const void *legacy_sync,
                                   const void *dmabuf_sync)
    __asm__("_ZN15BufferAllocator10CpuSyncEndEj8SyncTypeRKNSt3__18functionIFiiiPvEEES3_");

void luma_buffer_allocator_ctor(void *self) { (void)self; }

int luma_buffer_allocator_alloc(void *self, const void *heap_name, size_t size,
                                unsigned int flags, size_t alignment) {
  (void)self;
  (void)heap_name;
  (void)flags;
  (void)alignment;
  return luma_qcomtee_allocate(size);
}

int luma_buffer_allocator_sync_start(void *self, unsigned int fd, int sync_type,
                                     const void *legacy_sync,
                                     const void *dmabuf_sync) {
  (void)self;
  (void)fd;
  (void)sync_type;
  (void)legacy_sync;
  (void)dmabuf_sync;
  return 0;
}

int luma_buffer_allocator_sync_end(void *self, unsigned int fd, int sync_type,
                                   const void *legacy_sync,
                                   const void *dmabuf_sync) {
  (void)self;
  (void)fd;
  (void)sync_type;
  (void)legacy_sync;
  (void)dmabuf_sync;
  return 0;
}
