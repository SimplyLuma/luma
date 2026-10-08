// SPDX-License-Identifier: Apache-2.0

/*
 * Minimal Android libdmabufheap ABI adapter for Luma's QCOMTEE client.
 *
 * The stock QREL qseecomd imports BufferAllocator::Alloc plus both the C and
 * C++ CPU-sync entry points. QCOMTEE returns coherent shared-memory file
 * descriptors, so the sync operations are intentional no-ops. Keep this
 * library freestanding: luma_qcomtee_allocate is supplied by the accepted
 * libminkdescriptor adapter loaded in the same Android linker namespace.
 */

#include <stddef.h>
#include <stdint.h>

extern int luma_qcomtee_allocate(size_t size);

__attribute__((visibility("default")))
void buffer_allocator_ctor(void)
    __asm__("_ZN15BufferAllocatorC1Ev");

void buffer_allocator_ctor(void)
{
}

__attribute__((visibility("default")))
int buffer_allocator_alloc(void *self, const void *heap_name, size_t size,
                           uint32_t heap_flags, size_t legacy_align)
    __asm__("_ZN15BufferAllocator5AllocERKNSt3__112basic_stringIcNS0_11char_traitsIcEENS0_9allocatorIcEEEEmjm");

int buffer_allocator_alloc(void *self, const void *heap_name, size_t size,
                           uint32_t heap_flags, size_t legacy_align)
{
    (void)self;
    (void)heap_name;
    (void)heap_flags;
    (void)legacy_align;
    return luma_qcomtee_allocate(size);
}

__attribute__((visibility("default")))
int buffer_allocator_cpu_sync_start(void *self, uint32_t fd, int sync_type,
                                    const void *legacy_sync,
                                    const void *legacy_data)
    __asm__("_ZN15BufferAllocator12CpuSyncStartEj8SyncTypeRKNSt3__18functionIFiiiPvEEES3_");

int buffer_allocator_cpu_sync_start(void *self, uint32_t fd, int sync_type,
                                    const void *legacy_sync,
                                    const void *legacy_data)
{
    (void)self;
    (void)fd;
    (void)sync_type;
    (void)legacy_sync;
    (void)legacy_data;
    return 0;
}

__attribute__((visibility("default")))
int buffer_allocator_cpu_sync_end(void *self, uint32_t fd, int sync_type,
                                  const void *legacy_sync,
                                  const void *legacy_data)
    __asm__("_ZN15BufferAllocator10CpuSyncEndEj8SyncTypeRKNSt3__18functionIFiiiPvEEES3_");

int buffer_allocator_cpu_sync_end(void *self, uint32_t fd, int sync_type,
                                  const void *legacy_sync,
                                  const void *legacy_data)
{
    (void)self;
    (void)fd;
    (void)sync_type;
    (void)legacy_sync;
    (void)legacy_data;
    return 0;
}

__attribute__((visibility("default")))
int DmabufHeapCpuSyncStart(uint32_t fd, int sync_type,
                          const void *legacy_sync, const void *legacy_data)
{
    (void)fd;
    (void)sync_type;
    (void)legacy_sync;
    (void)legacy_data;
    return 0;
}

__attribute__((visibility("default")))
int DmabufHeapCpuSyncEnd(uint32_t fd, int sync_type,
                        const void *legacy_sync, const void *legacy_data)
{
    (void)fd;
    (void)sync_type;
    (void)legacy_sync;
    (void)legacy_data;
    return 0;
}
