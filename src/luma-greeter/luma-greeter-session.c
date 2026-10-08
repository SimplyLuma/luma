// SPDX-License-Identifier: Apache-2.0

/* Native Cage child for the Luma Presence greeter.
 *
 * Cage intentionally has no desktop settings daemon.  On panels that expose
 * native pixels at output scale 1, configure the compositor through wlroots'
 * supported output-management client before starting GTK.  This runs before
 * authentication and before any user session; it is not a login-time replay
 * or a per-user preference.
 */

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define LUMA_GREETER "/usr/bin/luma-greeter"
#define WLR_RANDR "/usr/bin/wlr-randr"

static int valid_output(const char *value) {
  if (value == NULL || value[0] == '\0' || strlen(value) > 63)
    return 0;
  for (const unsigned char *p = (const unsigned char *)value; *p != '\0'; ++p)
    if (!isalnum(*p) && *p != '-' && *p != '_' && *p != '.')
      return 0;
  return 1;
}

static int valid_scale(const char *value) {
  return value != NULL &&
         (strcmp(value, "1") == 0 || strcmp(value, "2") == 0 ||
          strcmp(value, "3") == 0 || strcmp(value, "4") == 0);
}

static void configure_output(const char *output, const char *scale) {
  if (!valid_output(output) || !valid_scale(scale)) {
    fputs("luma-greeter-session: invalid output configuration; using the "
          "compositor default\n",
          stderr);
    return;
  }

  pid_t child = fork();
  if (child < 0) {
    perror("luma-greeter-session: fork");
    return;
  }
  if (child == 0) {
    execl(WLR_RANDR, "wlr-randr", "--output", output, "--scale", scale,
          (char *)NULL);
    _exit(127);
  }

  int status = 0;
  while (waitpid(child, &status, 0) < 0) {
    if (errno == EINTR)
      continue;
    perror("luma-greeter-session: waitpid");
    return;
  }
  if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
    fputs("luma-greeter-session: output configuration failed; using the "
          "compositor default\n",
          stderr);
}

int main(void) {
  const char *output = getenv("LUMA_GREETER_OUTPUT");
  const char *scale = getenv("LUMA_GREETER_SCALE");
  if (output != NULL || scale != NULL)
    configure_output(output, scale);

  execl(LUMA_GREETER, "luma-greeter", (char *)NULL);
  perror("luma-greeter-session: exec luma-greeter");
  return 127;
}
