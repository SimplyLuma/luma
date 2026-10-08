// SPDX-License-Identifier: Apache-2.0
/*
 * Create Android init's inherited ims_datad control socket inside a disposable
 * chroot, drop to AID_RADIO, and execute only the stock FP6 IMS daemon.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>

static const uid_t aid_system = 1000;
static const uid_t aid_radio = 1001;

static void die(const char *operation) {
  dprintf(STDERR_FILENO, "event=luma_ims_socket_launcher_failed operation=%s errno=%d\n",
          operation, errno);
  _exit(125);
}

int main(int argc, char **argv) {
  static const char socket_path[] = "/dev/socket/ims_datad";
  static const char root_prefix[] = "/var/tmp/luma-stock-ims-root.";
  static const char library_path[] =
      "/luma-adapter:/apex/com.android.runtime/lib64/bionic:/vendor/lib64:"
      "/vendor/lib64/hw:/system/lib64:/system_ext/lib64:/product/lib64";
  static char *const daemon_argv[] = {
      "/system/bin/sh", "-c",
      "export LD_PRELOAD=/luma-adapter/libluma-ims-properties.so; exec "
      "/vendor/bin/imsdaemon",
      NULL};
  struct sockaddr_un address;
  int descriptor;
  int flags;

  if (argc != 2 || !argv[1] ||
      strncmp(argv[1], root_prefix, sizeof(root_prefix) - 1) != 0) {
    errno = EINVAL;
    die("root_identity");
  }
  if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0)
    die("pdeathsig");
  if (chroot(argv[1]) != 0 || chdir("/") != 0)
    die("chroot");

  descriptor = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (descriptor < 0)
    die("socket");
  memset(&address, 0, sizeof(address));
  address.sun_family = AF_UNIX;
  memcpy(address.sun_path, socket_path, sizeof(socket_path));
  unlink(socket_path);
  if (bind(descriptor, (struct sockaddr *)&address,
           offsetof(struct sockaddr_un, sun_path) + sizeof(socket_path)) != 0)
    die("bind");
  if (chown(socket_path, aid_system, aid_radio) != 0 ||
      chmod(socket_path, 0660) != 0)
    die("socket_permissions");
  if (listen(descriptor, 4) != 0)
    die("listen");

  if (descriptor != 3) {
    if (dup2(descriptor, 3) < 0)
      die("dup2");
    close(descriptor);
    descriptor = 3;
  }
  flags = fcntl(descriptor, F_GETFD);
  if (flags < 0 || fcntl(descriptor, F_SETFD, flags & ~FD_CLOEXEC) != 0)
    die("fd_flags");

  if (clearenv() != 0 || setenv("ANDROID_SOCKET_ims_datad", "3", 1) != 0 ||
      setenv("LD_LIBRARY_PATH", library_path, 1) != 0)
    die("environment");
  if (setgroups(0, NULL) != 0 || setgid(aid_radio) != 0 ||
      setuid(aid_radio) != 0)
    die("drop_privileges");
#ifdef SYS_close_range
  (void)syscall(SYS_close_range, 4U, ~0U, 0U);
#endif
  execve(daemon_argv[0], daemon_argv, environ);
  die("execve");
}
