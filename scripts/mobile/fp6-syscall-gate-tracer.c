// SPDX-License-Identifier: Apache-2.0
// Minimal AArch64 ptrace helper for bounded early-start diagnostics.  It logs
// only failed pathname syscalls, binder ioctls, and terminating signals.

#define _GNU_SOURCE
#include <elf.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <unistd.h>

struct arm64_regs {
  uint64_t regs[31];
  uint64_t sp;
  uint64_t pc;
  uint64_t pstate;
};

static void read_remote_string(pid_t pid, uint64_t address, char *output,
                               size_t capacity) {
  size_t offset = 0;
  output[0] = '\0';
  while (offset + sizeof(long) <= capacity) {
    errno = 0;
    long word = ptrace(PTRACE_PEEKDATA, pid, (void *)(address + offset), NULL);
    if (errno != 0)
      break;
    memcpy(output + offset, &word, sizeof(word));
    if (memchr(&word, '\0', sizeof(word)) != NULL)
      return;
    offset += sizeof(word);
  }
  output[capacity - 1] = '\0';
}

static int get_regs(pid_t pid, struct arm64_regs *regs) {
  struct iovec io = {.iov_base = regs, .iov_len = sizeof(*regs)};
  return ptrace(PTRACE_GETREGSET, pid, (void *)NT_PRSTATUS, &io);
}

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: %s command [args...]\n", argv[0]);
    return 2;
  }
  pid_t child = fork();
  if (child < 0)
    return 2;
  if (child == 0) {
    ptrace(PTRACE_TRACEME, 0, NULL, NULL);
    raise(SIGSTOP);
    execvp(argv[1], &argv[1]);
    _exit(127);
  }

  int status = 0;
  int entering = 1;
  uint64_t syscall_number = 0;
  uint64_t args[6] = {0};
  int nanosleep_context_reported = 0;
  waitpid(child, &status, 0);
  ptrace(PTRACE_SETOPTIONS, child, NULL, PTRACE_O_TRACESYSGOOD);
  ptrace(PTRACE_SYSCALL, child, NULL, NULL);

  while (waitpid(child, &status, 0) > 0) {
    if (WIFEXITED(status)) {
      printf("event=trace_exit status=%d\n", WEXITSTATUS(status));
      return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
      printf("event=trace_signal signal=%d\n", WTERMSIG(status));
      return 128 + WTERMSIG(status);
    }
    int signal_number = WSTOPSIG(status);
    if (signal_number == (SIGTRAP | 0x80)) {
      struct arm64_regs regs;
      if (get_regs(child, &regs) == 0) {
        if (entering) {
          syscall_number = regs.regs[8];
          memcpy(args, regs.regs, sizeof(args));
        } else {
          int64_t result = (int64_t)regs.regs[0];
          if (syscall_number == 29 || syscall_number == 101 || result < 0) {
            char path[256] = "";
            if (syscall_number == 56)
              read_remote_string(child, args[1], path, sizeof(path));
            else if (syscall_number == 48 || syscall_number == 49 ||
                     syscall_number == 79)
              read_remote_string(child, args[0], path, sizeof(path));
            if (syscall_number == 101) {
              errno = 0;
              long seconds = ptrace(PTRACE_PEEKDATA, child,
                                    (void *)(uintptr_t)args[0], NULL);
              long nanoseconds = ptrace(PTRACE_PEEKDATA, child,
                                        (void *)(uintptr_t)(args[0] + 8), NULL);
              long caller = ptrace(PTRACE_PEEKDATA, child,
                                   (void *)(uintptr_t)(regs.sp + 40), NULL);
              printf("event=syscall number=101 result=%lld seconds=%ld nanoseconds=%ld pc=0x%llx lr=0x%llx caller=0x%lx\n",
                     (long long)result, seconds, nanoseconds,
                     (unsigned long long)regs.pc,
                     (unsigned long long)regs.regs[30], caller);
              if (!nanosleep_context_reported) {
                char maps_path[64];
                char line[512];
                snprintf(maps_path, sizeof(maps_path), "/proc/%d/maps", child);
                FILE *maps = fopen(maps_path, "r");
                while (maps && fgets(line, sizeof(line), maps))
                  printf("event=trace_map %s", line);
                if (maps)
                  fclose(maps);
                nanosleep_context_reported = 1;
              }
            } else {
              printf("event=syscall number=%llu result=%lld arg0=0x%llx arg1=0x%llx path=%s\n",
                     (unsigned long long)syscall_number, (long long)result,
                     (unsigned long long)args[0], (unsigned long long)args[1],
                     path);
            }
            fflush(stdout);
          }
        }
        entering = !entering;
      }
      ptrace(PTRACE_SYSCALL, child, NULL, NULL);
    } else if (signal_number == SIGTRAP) {
      ptrace(PTRACE_SYSCALL, child, NULL, NULL);
    } else {
      printf("event=trace_stop signal=%d\n", signal_number);
      fflush(stdout);
      ptrace(PTRACE_SYSCALL, child, NULL, (void *)(intptr_t)signal_number);
    }
  }
  return 2;
}
