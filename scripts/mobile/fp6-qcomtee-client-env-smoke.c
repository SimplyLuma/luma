// SPDX-License-Identifier: BSD-3-Clause
// Bounded client-environment smoke for Qualcomm's upstream libqcomtee.
// It opens no biometric service and invokes no biometric command.

#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>

#include <qcomtee_object.h>

struct worker_state {
  pthread_t thread;
  struct qcomtee_object *root;
};

static int tee_call(int fd, unsigned long operation, ...) {
  va_list args;
  void *argument;
  int result;

  va_start(args, operation);
  argument = va_arg(args, void *);
  va_end(args);
  pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);
  result = ioctl(fd, operation, argument);
  pthread_setcanceltype(PTHREAD_CANCEL_DEFERRED, NULL);
  return result;
}

static void *worker_main(void *argument) {
  struct worker_state *state = argument;
  while (qcomtee_object_process_one(state->root) == 0)
    pthread_testcancel();
  return NULL;
}

static void release_worker(void *argument) {
  struct worker_state *state = argument;
  if (state->thread) {
    pthread_cancel(state->thread);
    pthread_join(state->thread, NULL);
  }
  free(state);
}

int main(void) {
  struct worker_state *state = calloc(1, sizeof(*state));
  struct qcomtee_object *client_environment = QCOMTEE_OBJECT_NULL;
  struct qcomtee_param parameters[2] = {0};
  qcomtee_result_t secure_result = QCOMTEE_ERROR;

  if (!state) {
    fprintf(stderr, "event=qcomtee_client_env_failed reason=allocation\n");
    return 1;
  }
  state->root = qcomtee_object_root_init("/dev/tee0", tee_call,
                                         release_worker, state);
  if (state->root == QCOMTEE_OBJECT_NULL) {
    fprintf(stderr, "event=qcomtee_client_env_failed reason=root_open errno=%d\n",
            errno);
    free(state);
    return 1;
  }
  if (pthread_create(&state->thread, NULL, worker_main, state) != 0) {
    fprintf(stderr, "event=qcomtee_client_env_failed reason=worker\n");
    qcomtee_object_refs_dec(state->root);
    return 1;
  }

  parameters[0].attr = QCOMTEE_OBJREF_INPUT;
  parameters[0].object = QCOMTEE_OBJECT_NULL;
  parameters[1].attr = QCOMTEE_OBJREF_OUTPUT;
  if (qcomtee_object_invoke(state->root, 2, parameters, 2,
                            &secure_result) != 0 ||
      secure_result != QCOMTEE_OK ||
      parameters[1].object == QCOMTEE_OBJECT_NULL) {
    fprintf(stderr,
            "event=qcomtee_client_env_failed reason=secure_registration result=%d\n",
            secure_result);
    qcomtee_object_refs_dec(state->root);
    return 1;
  }

  client_environment = parameters[1].object;
  printf("event=qcomtee_client_env_ready transport=/dev/tee0 registration=accepted biometric_commands=0 protected_state_written=false\n");
  qcomtee_object_refs_dec(client_environment);
  qcomtee_object_refs_dec(state->root);
  return 0;
}
