// SPDX-License-Identifier: Apache-2.0
// Sample only AArch64 PC/LR and their executable mappings from a stopped PID.

#include <elf.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ptrace.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <sys/wait.h>

struct arm64_regs {
  uint64_t regs[31];
  uint64_t sp;
  uint64_t pc;
  uint64_t pstate;
};

static void report_mapping(pid_t pid, const char *label, uint64_t address) {
  char path[64];
  char line[1024];
  unsigned long long start, end, offset;
  char permissions[8];
  snprintf(path, sizeof(path), "/proc/%d/maps", pid);
  FILE *maps = fopen(path, "r");
  while (maps && fgets(line, sizeof(line), maps)) {
    if (sscanf(line, "%llx-%llx %7s %llx", &start, &end, permissions,
               &offset) == 4 &&
        address >= start && address < end) {
      printf("event=pc_sample target=%s address=0x%llx file_offset=0x%llx map=%s",
             label, (unsigned long long)address,
             (unsigned long long)(offset + address - start), line);
      fclose(maps);
      return;
    }
  }
  if (maps)
    fclose(maps);
  printf("event=pc_sample target=%s address=0x%llx map=unknown\n", label,
         (unsigned long long)address);
}

static uint64_t canonical_code_address(uint64_t address) {
  return address & 0x0000ffffffffffffULL;
}

int main(int argc, char **argv) {
  struct arm64_regs regs;
  struct iovec io = {.iov_base = &regs, .iov_len = sizeof(regs)};
  int status;
  pid_t pid;
  if (argc != 2)
    return 64;
  pid = (pid_t)strtol(argv[1], NULL, 10);
  if (pid <= 0 || ptrace(PTRACE_ATTACH, pid, NULL, NULL) != 0)
    return 2;
  if (waitpid(pid, &status, 0) != pid || !WIFSTOPPED(status)) {
    ptrace(PTRACE_DETACH, pid, NULL, NULL);
    return 2;
  }
  if (ptrace(PTRACE_GETREGSET, pid, (void *)NT_PRSTATUS, &io) != 0) {
    ptrace(PTRACE_DETACH, pid, NULL, NULL);
    return 2;
  }
  report_mapping(pid, "pc", regs.pc);
  report_mapping(pid, "lr", regs.regs[30]);
  uint64_t frame = regs.regs[29];
  for (unsigned int index = 0; index < 8 && frame; ++index) {
    errno = 0;
    long next_frame = ptrace(PTRACE_PEEKDATA, pid, (void *)frame, NULL);
    if (errno != 0)
      break;
    errno = 0;
    long return_address =
        ptrace(PTRACE_PEEKDATA, pid, (void *)(frame + 8), NULL);
    if (errno != 0)
      break;
    char label[32];
    snprintf(label, sizeof(label), "frame%u", index);
    report_mapping(pid, label,
                   canonical_code_address((uint64_t)return_address));
    if ((uint64_t)next_frame <= frame || (uint64_t)next_frame - frame > 1048576)
      break;
    frame = (uint64_t)next_frame;
  }
  ptrace(PTRACE_DETACH, pid, NULL, NULL);
  return 0;
}
