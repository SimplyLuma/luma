// SPDX-License-Identifier: Apache-2.0

/*
 * Emit one root-only virtual KEY_WAKEUP press after a secure fingerprint
 * match.  Phosh's public WakeUpScreen signal wakes gsd-power, but it does not
 * reset the compositor's own idle timer.  A real input event does, matching
 * the semantics of the FP6 power key without pretending to authenticate.
 */

#include <errno.h>
#include <fcntl.h>
#include <linux/input-event-codes.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int emit_event(int fd, unsigned short type, unsigned short code,
                      int value) {
  struct input_event event = {0};

  event.type = type;
  event.code = code;
  event.value = value;
  return write(fd, &event, sizeof(event)) == (ssize_t)sizeof(event) ? 0 : -1;
}

int main(void) {
  const char *device = "/dev/uinput";
  struct uinput_setup setup = {0};
  int fd;

  if (geteuid() != 0) {
    fputs("luma-fp6-wake-input: root required\n", stderr);
    return 1;
  }
  fd = open(device, O_WRONLY | O_CLOEXEC | O_NONBLOCK);
  if (fd < 0) {
    fprintf(stderr, "luma-fp6-wake-input: open: %s\n", strerror(errno));
    return 2;
  }
  if (ioctl(fd, UI_SET_EVBIT, EV_KEY) < 0 ||
      ioctl(fd, UI_SET_KEYBIT, KEY_WAKEUP) < 0 ||
      ioctl(fd, UI_SET_EVBIT, EV_SYN) < 0) {
    fprintf(stderr, "luma-fp6-wake-input: configure: %s\n", strerror(errno));
    close(fd);
    return 3;
  }
  setup.id.bustype = BUS_VIRTUAL;
  setup.id.vendor = 0x1d6b;
  setup.id.product = 0x4c55;
  snprintf(setup.name, sizeof(setup.name), "Luma Fingerprint Wake");
  if (ioctl(fd, UI_DEV_SETUP, &setup) < 0 || ioctl(fd, UI_DEV_CREATE) < 0) {
    fprintf(stderr, "luma-fp6-wake-input: create: %s\n", strerror(errno));
    close(fd);
    return 4;
  }

  /* Let udev/libinput attach the new seat device before sending its event. */
  usleep(150000);
  if (emit_event(fd, EV_KEY, KEY_WAKEUP, 1) < 0 ||
      emit_event(fd, EV_SYN, SYN_REPORT, 0) < 0 ||
      emit_event(fd, EV_KEY, KEY_WAKEUP, 0) < 0 ||
      emit_event(fd, EV_SYN, SYN_REPORT, 0) < 0) {
    fprintf(stderr, "luma-fp6-wake-input: emit: %s\n", strerror(errno));
    ioctl(fd, UI_DEV_DESTROY);
    close(fd);
    return 5;
  }
  usleep(150000);
  ioctl(fd, UI_DEV_DESTROY);
  close(fd);
  return 0;
}
