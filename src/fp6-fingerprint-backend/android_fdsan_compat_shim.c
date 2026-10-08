// SPDX-License-Identifier: Apache-2.0
/*
 * The QREL FocalTech HAL is a debug build.  In Luma's isolated stock-service
 * namespace it closes an untagged fd 0 during startup and Bionic's debug
 * fdsan policy aborts the entire service.  Keep fdsan reporting enabled but
 * make this one ephemeral process warn once so the real HAL can finish its
 * non-persistent initialization while the ownership mismatch remains visible.
 */

enum android_fdsan_error_level {
  ANDROID_FDSAN_ERROR_LEVEL_DISABLED = 0,
  ANDROID_FDSAN_ERROR_LEVEL_WARN_ONCE = 1,
};

extern enum android_fdsan_error_level android_fdsan_set_error_level(
    enum android_fdsan_error_level new_level);
extern enum android_fdsan_error_level android_fdsan_get_error_level(void);
extern int dprintf(int fd, const char *format, ...);
typedef void (*luma_signal_handler)(int);
extern luma_signal_handler signal(int signal_number, luma_signal_handler handler);
extern int backtrace(void **buffer, int size);
extern void backtrace_symbols_fd(void *const *buffer, int size, int fd);
extern void _exit(int status);
extern long syscall(long number, ...);
static unsigned int close_mismatch_count;

/* AArch64 Linux __NR_close.  This symbol is preloaded only into the isolated
 * stock fingerprint process, never into Luma or another system service. */
int android_fdsan_close_with_tag(int fd, unsigned long long tag) {
  unsigned int sequence = close_mismatch_count++;
  if (sequence < 8)
    dprintf(2,
            "event=luma_fdsan_close_compat sequence=%u fd=%d tagged=%s\n",
            sequence, fd, tag ? "true" : "false");
  return (int)syscall(57, fd);
}

static void luma_abort_trace(int signal_number) {
  void *frames[64];
  int count = backtrace(frames, 64);
  dprintf(2, "event=luma_fdsan_abort_trace signal=%d frames=%d\n",
          signal_number, count);
  backtrace_symbols_fd(frames, count, 2);
  _exit(134);
}

__attribute__((constructor)) static void luma_fdsan_compat_init(void) {
  enum android_fdsan_error_level previous =
      android_fdsan_set_error_level(ANDROID_FDSAN_ERROR_LEVEL_DISABLED);
  (void)signal(6, luma_abort_trace);
  dprintf(2, "event=luma_fdsan_compat previous=%d current=%d\n", previous,
          android_fdsan_get_error_level());
}
