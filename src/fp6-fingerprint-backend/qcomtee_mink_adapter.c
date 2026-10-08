// SPDX-License-Identifier: BSD-3-Clause
/*
 * Narrow Android-Mink to upstream Linux QCOM-TEE adapter for the FP6.
 *
 * QREL's unmodified secure services expect TZCom's two-object ABI.  Linux's
 * upstream QCOM-TEE driver exposes the same secure-world object protocol via
 * TEE_IOC_OBJECT_INVOKE.  This adapter translates only those object calls and
 * dma-buf memory objects; it contains no biometric protocol or credential
 * policy.
 */

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#include <linux/tee.h>
#include <qcomtee_object.h>
#include <qcomtee_object_private.h>

#define OBJECT_METHOD_MASK 0xffffu
#define OBJECT_RELEASE 0xffffu
#define OBJECT_RETAIN 0xfffeu
#define OBJECT_ERROR 1
#define OBJECT_ERROR_BADOBJ (-92)
#define OBJECT_ERROR_MAXARGS (-94)
#define OBJECT_ERROR_KMEM (-97)
#define OBJECT_ERROR_UNAVAIL (-96)
#define OBJECT_ERROR_BUSY (-99)
#define OBJECT_MAX_PARAMS 10

/* GCC's glibc headers lower errno to __errno_location; Bionic exports __errno. */
extern int *__errno(void);
int *luma_errno_location(void) __asm__("__errno_location");
int *luma_errno_location(void) { return __errno(); }

union object_arg;
typedef int32_t (*object_invoke_fn)(void *, uint32_t, union object_arg *,
                                    uint32_t);

struct object {
  object_invoke_fn invoke;
  void *context;
};

struct object_buffer {
  void *address;
  size_t size;
};

struct object_input_buffer {
  const void *address;
  size_t size;
};

union object_arg {
  struct object_buffer buffer;
  struct object_input_buffer input_buffer;
  struct object object;
};

struct adapter_object {
  atomic_int references;
  struct qcomtee_object *object;
};

struct adapter_worker {
  pthread_t thread;
  struct qcomtee_object *root;
};

struct adapter_credentials {
  struct qcomtee_object object;
  unsigned char data[17];
  size_t size;
};

struct adapter_memory {
  struct qcomtee_object object;
  int shared_memory_fd;
};

struct adapter_native_callback {
  struct qcomtee_object object;
  struct object target;
};

struct allocation_record {
  dev_t device;
  ino_t inode;
  int object_id;
  size_t size;
  struct allocation_record *next;
};

struct timed_retry_forwarder {
  int references;
  int busy_reported;
  size_t timeout_ms;
  size_t interval_ms;
  struct object target;
  pthread_mutex_t mutex;
};

static struct qcomtee_object *latest_root;
static struct allocation_record *allocations;
static atomic_uint diagnostic_sequence;

#define ADAPTER_DIAGNOSTIC_PATH "/data/luma-qcomtee-adapter.log"

static void diagnostic_event(const char *event, uint32_t operation,
                             uint32_t counts, int transport_result,
                             int32_t secure_result) {
  unsigned int sequence = atomic_fetch_add(&diagnostic_sequence, 1);
  int diagnostic_fd;
  char diagnostic_line[256];
  int diagnostic_length;
  /* Metadata only: never log argument buffers or protected object contents. */
  if (sequence < 96 || transport_result != 0 || secure_result != 0) {
    diagnostic_fd = open(ADAPTER_DIAGNOSTIC_PATH,
                         O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (diagnostic_fd >= 0) {
      diagnostic_length = snprintf(
          diagnostic_line, sizeof(diagnostic_line),
          "event=luma_qcomtee_adapter sequence=%u stage=%s "
          "operation=0x%x counts=0x%x transport=%d secure=%d\n",
          sequence, event, operation, counts, transport_result, secure_result);
      if (diagnostic_length > 0) {
        size_t bytes = (size_t)diagnostic_length;
        if (bytes >= sizeof(diagnostic_line))
          bytes = sizeof(diagnostic_line) - 1;
        (void)write(diagnostic_fd, diagnostic_line, bytes);
      }
      close(diagnostic_fd);
    }
  }
}

static unsigned int input_buffers(uint32_t counts);
static unsigned int output_buffers(uint32_t counts);
static unsigned int input_objects(uint32_t counts);
static unsigned int output_objects(uint32_t counts);

int32_t TimedRetryForwarder_new(size_t timeout_ms, size_t interval_ms,
                                struct object target, struct object *output)
    __asm__("_Z23TimedRetryForwarder_newmm6ObjectPS_");

static int32_t timed_retry_invoke(void *context, uint32_t operation,
                                  union object_arg *arguments,
                                  uint32_t counts);

static int64_t monotonic_ms(void) {
  struct timespec now;
  if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
    return 0;
  return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static void sleep_ms(size_t milliseconds) {
  struct timespec delay = {
      .tv_sec = (time_t)(milliseconds / 1000),
      .tv_nsec = (long)((milliseconds % 1000) * 1000000),
  };
  while (nanosleep(&delay, &delay) != 0 && errno == EINTR)
    ;
}

static int32_t timed_retry_release(struct timed_retry_forwarder *forwarder) {
  struct object target = {0};
  int destroy = 0;

  if (pthread_mutex_lock(&forwarder->mutex) != 0)
    return OBJECT_ERROR_UNAVAIL;
  if (--forwarder->references == 0) {
    target = forwarder->target;
    forwarder->target = (struct object){0};
    destroy = 1;
  }
  pthread_mutex_unlock(&forwarder->mutex);
  if (destroy) {
    if (target.invoke)
      target.invoke(target.context, OBJECT_RELEASE, NULL, 0);
    pthread_mutex_destroy(&forwarder->mutex);
    free(forwarder);
  }
  return 0;
}

static int32_t timed_retry_invoke(void *context, uint32_t operation,
                                  union object_arg *arguments,
                                  uint32_t counts) {
  struct timed_retry_forwarder *forwarder = context;
  int64_t deadline;
  int32_t result;
  unsigned int first_output_object;
  unsigned int end_output_object;
  unsigned int index;

  if (!forwarder)
    return OBJECT_ERROR_BADOBJ;
  if ((operation & OBJECT_METHOD_MASK) == OBJECT_RETAIN) {
    if (pthread_mutex_lock(&forwarder->mutex) != 0)
      return OBJECT_ERROR_UNAVAIL;
    ++forwarder->references;
    pthread_mutex_unlock(&forwarder->mutex);
    return 0;
  }
  if ((operation & OBJECT_METHOD_MASK) == OBJECT_RELEASE)
    return timed_retry_release(forwarder);
  if (pthread_mutex_lock(&forwarder->mutex) != 0)
    return OBJECT_ERROR_UNAVAIL;

  deadline = monotonic_ms() + (int64_t)forwarder->timeout_ms;
  do {
    result = forwarder->target.invoke(forwarder->target.context, operation,
                                      arguments, counts);
    if (result == OBJECT_ERROR_BUSY && !forwarder->busy_reported) {
      dprintf(STDERR_FILENO,
              "luma_qcomtee event=secure_object_busy op=0x%x counts=0x%x "
              "timeout_ms=%zu interval_ms=%zu\n",
              operation, counts, forwarder->timeout_ms,
              forwarder->interval_ms);
      forwarder->busy_reported = 1;
    }
    if (result != OBJECT_ERROR_BUSY || monotonic_ms() >= deadline)
      break;
    sleep_ms(forwarder->interval_ms);
  } while (monotonic_ms() < deadline);

  if (result == 0) {
    first_output_object = input_buffers(counts) + output_buffers(counts) +
                          input_objects(counts);
    end_output_object = first_output_object + output_objects(counts);
    for (index = first_output_object; index < end_output_object; ++index) {
      struct object wrapped = {0};
      if (arguments[index].object.invoke != forwarder->target.invoke)
        continue;
      result = TimedRetryForwarder_new(forwarder->timeout_ms,
                                       forwarder->interval_ms,
                                       arguments[index].object, &wrapped);
      if (result != 0)
        break;
      arguments[index].object = wrapped;
    }
  }
  pthread_mutex_unlock(&forwarder->mutex);
  return result;
}

int32_t TimedRetryForwarder_new(size_t timeout_ms, size_t interval_ms,
                                struct object target, struct object *output) {
  struct timed_retry_forwarder *forwarder;

  if (!target.invoke || !output)
    return OBJECT_ERROR_BADOBJ;
  forwarder = calloc(1, sizeof(*forwarder));
  if (!forwarder)
    return OBJECT_ERROR_KMEM;
  if (pthread_mutex_init(&forwarder->mutex, NULL) != 0) {
    free(forwarder);
    return OBJECT_ERROR_UNAVAIL;
  }
  forwarder->references = 1;
  forwarder->timeout_ms = timeout_ms;
  forwarder->interval_ms = interval_ms;
  forwarder->target = target;
  *output = (struct object){timed_retry_invoke, forwarder};
  return 0;
}

static unsigned int input_buffers(uint32_t counts) { return counts & 0xf; }
static unsigned int output_buffers(uint32_t counts) {
  return (counts >> 4) & 0xf;
}
static unsigned int input_objects(uint32_t counts) {
  return (counts >> 8) & 0xf;
}
static unsigned int output_objects(uint32_t counts) {
  return (counts >> 12) & 0xf;
}

static int tee_call(int fd, unsigned long operation, ...) {
  va_list arguments;
  void *argument;
  int result;
  int saved_errno;

  va_start(arguments, operation);
  argument = va_arg(arguments, void *);
  va_end(arguments);
  result = ioctl(fd, operation, argument);
  saved_errno = errno;
  if (result != 0)
    diagnostic_event("tee_ioctl_failure", (uint32_t)operation, 0, result,
                     saved_errno);
  errno = saved_errno;
  return result;
}

static void *worker_main(void *argument) {
  struct adapter_worker *worker = argument;
  int result;
  do {
    result = qcomtee_object_process_one(worker->root);
  } while (result == 0);
  diagnostic_event("callback_worker_exit", 0, 0, result, 0);
  return NULL;
}

static struct qcomtee_object *new_root(void) {
  struct adapter_worker *worker = calloc(1, sizeof(*worker));
  if (!worker)
    return QCOMTEE_OBJECT_NULL;
  worker->root = qcomtee_object_root_init("/dev/tee0", tee_call, NULL, NULL);
  if (worker->root == QCOMTEE_OBJECT_NULL) {
    diagnostic_event("root_init", 0, 0, -1, 0);
    free(worker);
    return QCOMTEE_OBJECT_NULL;
  }
  diagnostic_event("root_init", 0, 0, 0, 0);
  /* The detached callback worker owns one process-lifetime root reference. */
  qcomtee_object_refs_inc(worker->root);
  if (pthread_create(&worker->thread, NULL, worker_main, worker) != 0) {
    diagnostic_event("callback_worker_create", 0, 0, -1, 0);
    qcomtee_object_refs_dec(worker->root);
    qcomtee_object_refs_dec(worker->root);
    free(worker);
    return QCOMTEE_OBJECT_NULL;
  }
  diagnostic_event("callback_worker_create", 0, 0, 0, 0);
  pthread_detach(worker->thread);
  return worker->root;
}

static void credentials_release(struct qcomtee_object *object) {
  struct adapter_credentials *credentials =
      (struct adapter_credentials *)((char *)object -
                                     __builtin_offsetof(
                                         struct adapter_credentials, object));
  free(credentials);
}

static qcomtee_result_t credentials_dispatch(struct qcomtee_object *object,
                                             qcomtee_op_t operation,
                                             struct qcomtee_param *parameters,
                                             int count) {
  struct adapter_credentials *credentials =
      (struct adapter_credentials *)((char *)object -
                                     __builtin_offsetof(
                                         struct adapter_credentials, object));
  if (operation == 0) {
    if (count != 1 || parameters[0].attr != QCOMTEE_UBUF_OUTPUT)
      return QCOMTEE_ERROR_INVALID;
    parameters[0].ubuf.addr = &credentials->size;
    parameters[0].ubuf.size = sizeof(credentials->size);
    return QCOMTEE_OK;
  }
  if (operation == 1) {
    uint64_t offset;
    size_t available;
    if (count != 2 || parameters[0].attr != QCOMTEE_UBUF_INPUT ||
        parameters[1].attr != QCOMTEE_UBUF_OUTPUT ||
        parameters[0].ubuf.size < sizeof(offset))
      return QCOMTEE_ERROR_INVALID;
    memcpy(&offset, parameters[0].ubuf.addr, sizeof(offset));
    if (offset >= credentials->size)
      return QCOMTEE_ERROR_INVALID;
    available = credentials->size - (size_t)offset;
    parameters[1].ubuf.addr = credentials->data + offset;
    if (parameters[1].ubuf.size > available)
      parameters[1].ubuf.size = available;
    return QCOMTEE_OK;
  }
  return QCOMTEE_ERROR_INVALID;
}

static struct qcomtee_object_ops credentials_operations = {
    .release = credentials_release,
    .dispatch = credentials_dispatch,
};

static struct qcomtee_object *new_credentials(struct qcomtee_object *root) {
  struct adapter_credentials *credentials = calloc(1, sizeof(*credentials));
  struct timeval now;
  uint64_t milliseconds;
  uint32_t uid;
  unsigned int index;

  if (!credentials || gettimeofday(&now, NULL) != 0) {
    free(credentials);
    return QCOMTEE_OBJECT_NULL;
  }
  uid = (uint32_t)getuid();
  milliseconds = (uint64_t)now.tv_sec * 1000 + (uint64_t)now.tv_usec / 1000;
  /* Canonical CBOR map: { 1: uid, 6: current-time-in-milliseconds }. */
  credentials->data[0] = 0xa2;
  credentials->data[1] = 0x01;
  credentials->data[2] = 0x1a;
  credentials->data[3] = (unsigned char)(uid >> 24);
  credentials->data[4] = (unsigned char)(uid >> 16);
  credentials->data[5] = (unsigned char)(uid >> 8);
  credentials->data[6] = (unsigned char)uid;
  credentials->data[7] = 0x06;
  credentials->data[8] = 0x1b;
  for (index = 0; index < 8; ++index)
    credentials->data[9 + index] =
        (unsigned char)(milliseconds >> (56 - index * 8));
  credentials->size = sizeof(credentials->data);
  if (qcomtee_object_cb_init(&credentials->object, &credentials_operations,
                             root) != 0) {
    free(credentials);
    return QCOMTEE_OBJECT_NULL;
  }
  return &credentials->object;
}

static int32_t adapter_invoke(void *context, uint32_t operation,
                              union object_arg *arguments, uint32_t counts);

static struct object wrap_object(struct qcomtee_object *object) {
  struct adapter_object *adapter;
  if (object == QCOMTEE_OBJECT_NULL)
    return (struct object){0};
  adapter = calloc(1, sizeof(*adapter));
  if (!adapter)
    return (struct object){0};
  atomic_init(&adapter->references, 1);
  adapter->object = object;
  return (struct object){adapter_invoke, adapter};
}

static struct qcomtee_object *unwrap_object(struct object object) {
  struct adapter_object *adapter;
  if (!object.invoke)
    return QCOMTEE_OBJECT_NULL;
  if (object.invoke != adapter_invoke || !object.context)
    return QCOMTEE_OBJECT_NULL;
  adapter = object.context;
  return adapter->object;
}

static void native_callback_release(struct qcomtee_object *object) {
  struct adapter_native_callback *callback =
      container_of(object, struct adapter_native_callback, object);

  if (callback->target.invoke)
    callback->target.invoke(callback->target.context, OBJECT_RELEASE, NULL, 0);
  free(callback);
}

static qcomtee_result_t native_callback_dispatch(
    struct qcomtee_object *object, qcomtee_op_t operation,
    struct qcomtee_param *parameters, int count) {
  struct adapter_native_callback *callback =
      container_of(object, struct adapter_native_callback, object);
  union object_arg arguments[OBJECT_MAX_PARAMS] = {0};
  uint32_t counts = 0;
  int previous_group = -1;
  int index;
  int32_t result;

  if (!callback->target.invoke || count < 0 || count > OBJECT_MAX_PARAMS ||
      (count != 0 && !parameters))
    return QCOMTEE_ERROR_INVALID;

  /* Mink's wire ABI groups IB, OB, IO and OO in that order. Stock listener
   * callbacks use buffers only; reject object-bearing callbacks until their
   * ownership semantics are explicitly needed and tested. */
  for (index = 0; index < count; ++index) {
    int group;
    switch (parameters[index].attr) {
    case QCOMTEE_UBUF_INPUT:
      group = 0;
      arguments[index].input_buffer.address = parameters[index].ubuf.addr;
      arguments[index].input_buffer.size = parameters[index].ubuf.size;
      break;
    case QCOMTEE_UBUF_OUTPUT:
      group = 1;
      arguments[index].buffer.address = parameters[index].ubuf.addr;
      arguments[index].buffer.size = parameters[index].ubuf.size;
      break;
    default:
      return QCOMTEE_ERROR_INVALID;
    }
    if (group < previous_group)
      return QCOMTEE_ERROR_INVALID;
    previous_group = group;
    counts += 1u << (unsigned int)(group * 4);
  }

  result = callback->target.invoke(callback->target.context, operation,
                                   arguments, counts);
  diagnostic_event("native_callback_dispatch", operation, counts, 0, result);
  if (result != 0)
    return result;
  for (index = 0; index < count; ++index) {
    if (parameters[index].attr == QCOMTEE_UBUF_OUTPUT)
      parameters[index].ubuf.size = arguments[index].buffer.size;
  }
  return QCOMTEE_OK;
}

static struct qcomtee_object_ops native_callback_operations = {
    .release = native_callback_release,
    .dispatch = native_callback_dispatch,
};

static struct qcomtee_object *wrap_native_callback(
    struct object target, struct qcomtee_object *root) {
  struct adapter_native_callback *callback;

  if (!target.invoke || root == QCOMTEE_OBJECT_NULL)
    return QCOMTEE_OBJECT_NULL;
  if (target.invoke(target.context, OBJECT_RETAIN, NULL, 0) != 0)
    return QCOMTEE_OBJECT_NULL;
  callback = calloc(1, sizeof(*callback));
  if (!callback) {
    target.invoke(target.context, OBJECT_RELEASE, NULL, 0);
    return QCOMTEE_OBJECT_NULL;
  }
  callback->target = target;
  if (qcomtee_object_cb_init(&callback->object, &native_callback_operations,
                             root) != 0) {
    target.invoke(target.context, OBJECT_RELEASE, NULL, 0);
    free(callback);
    return QCOMTEE_OBJECT_NULL;
  }
  return &callback->object;
}

static int32_t adapter_invoke(void *context, uint32_t operation,
                              union object_arg *arguments, uint32_t counts) {
  struct adapter_object *adapter = context;
  struct qcomtee_param parameters[OBJECT_MAX_PARAMS] = {0};
  qcomtee_result_t secure_result = QCOMTEE_ERROR;
  unsigned int bi = input_buffers(counts);
  unsigned int bo = output_buffers(counts);
  unsigned int oi = input_objects(counts);
  unsigned int oo = output_objects(counts);
  unsigned int total = bi + bo + oi + oo;
  struct qcomtee_object *native_callbacks[OBJECT_MAX_PARAMS] = {0};
  unsigned int index;

  if (!adapter || !adapter->object)
    return OBJECT_ERROR_BADOBJ;
  if ((operation & OBJECT_METHOD_MASK) == OBJECT_RETAIN) {
    if (qcomtee_object_refs_inc(adapter->object) != 0)
      return OBJECT_ERROR_BADOBJ;
    atomic_fetch_add(&adapter->references, 1);
    return 0;
  }
  if ((operation & OBJECT_METHOD_MASK) == OBJECT_RELEASE) {
    qcomtee_object_refs_dec(adapter->object);
    if (atomic_fetch_sub(&adapter->references, 1) == 1) {
      adapter->object = QCOMTEE_OBJECT_NULL;
      free(adapter);
    }
    return 0;
  }
  if (total > OBJECT_MAX_PARAMS || (total && !arguments))
    return OBJECT_ERROR_MAXARGS;

  for (index = 0; index < bi; ++index) {
    parameters[index].attr = QCOMTEE_UBUF_INPUT;
    parameters[index].ubuf.addr = (void *)arguments[index].input_buffer.address;
    parameters[index].ubuf.size = arguments[index].input_buffer.size;
  }
  for (; index < bi + bo; ++index) {
    parameters[index].attr = QCOMTEE_UBUF_OUTPUT;
    parameters[index].ubuf.addr = arguments[index].buffer.address;
    parameters[index].ubuf.size = arguments[index].buffer.size;
  }
  for (; index < bi + bo + oi; ++index) {
    parameters[index].attr = QCOMTEE_OBJREF_INPUT;
    parameters[index].object = unwrap_object(arguments[index].object);
    if (arguments[index].object.invoke &&
        parameters[index].object == QCOMTEE_OBJECT_NULL) {
      parameters[index].object = wrap_native_callback(
          arguments[index].object, adapter->object->root);
      if (parameters[index].object == QCOMTEE_OBJECT_NULL) {
        unsigned int cleanup_index;
        for (cleanup_index = 0; cleanup_index < OBJECT_MAX_PARAMS;
             ++cleanup_index)
          if (native_callbacks[cleanup_index] != QCOMTEE_OBJECT_NULL)
            qcomtee_object_refs_dec(native_callbacks[cleanup_index]);
        return OBJECT_ERROR_BADOBJ;
      }
      native_callbacks[index] = parameters[index].object;
    }
    diagnostic_event(
        "input_object", index,
        (uint32_t)qcomtee_object_typeof(parameters[index].object),
        parameters[index].object == QCOMTEE_OBJECT_NULL ||
                parameters[index].object->root == adapter->object->root
            ? 0
            : -1,
        parameters[index].object == QCOMTEE_OBJECT_NULL
            ? 0
            : parameters[index].object->queued);
  }
  for (; index < total; ++index) {
    parameters[index].attr = QCOMTEE_OBJREF_OUTPUT;
    parameters[index].object = QCOMTEE_OBJECT_NULL;
    arguments[index].object = (struct object){0};
  }

  diagnostic_event(
      "target_object", operation,
      (uint32_t)qcomtee_object_typeof(adapter->object),
      adapter->object->root == QCOMTEE_OBJECT_NULL ? -1 : 0,
      adapter->object->queued);

  int transport_result = qcomtee_object_invoke(
      adapter->object, operation, parameters, total, &secure_result);
  diagnostic_event("object_invoke", operation, counts, transport_result,
                   secure_result);
  if (transport_result != 0) {
    for (index = bi + bo; index < bi + bo + oi; ++index) {
      diagnostic_event(
          "input_object_after_failure", index,
          (uint32_t)qcomtee_object_typeof(parameters[index].object),
          parameters[index].object == QCOMTEE_OBJECT_NULL
              ? -1
              : parameters[index].object->queued,
          parameters[index].object == QCOMTEE_OBJECT_NULL
              ? 0
              : (int32_t)parameters[index].object->object_id);
    }
    for (index = 0; index < OBJECT_MAX_PARAMS; ++index)
      if (native_callbacks[index] != QCOMTEE_OBJECT_NULL)
        qcomtee_object_refs_dec(native_callbacks[index]);
    return QCOMTEE_ERROR_UNAVAIL;
  }
  for (index = bi; index < bi + bo; ++index)
    arguments[index].buffer.size = parameters[index].ubuf.size;
  for (index = bi + bo + oi; index < total; ++index) {
    arguments[index].object = wrap_object(parameters[index].object);
    if (parameters[index].object != QCOMTEE_OBJECT_NULL &&
        !arguments[index].object.invoke) {
      qcomtee_object_refs_dec(parameters[index].object);
      return OBJECT_ERROR_KMEM;
    }
  }
  return (int32_t)secure_result;
}

static void adapter_memory_release(struct qcomtee_object *object) {
  struct adapter_memory *memory =
      (struct adapter_memory *)((char *)object -
                                __builtin_offsetof(struct adapter_memory,
                                                   object));
  if (memory->shared_memory_fd >= 0)
    close(memory->shared_memory_fd);
  free(memory);
}

static struct qcomtee_object_ops memory_operations = {
    .release = adapter_memory_release,
};

static struct qcomtee_object *register_dma_buffer(int fd,
                                                  struct qcomtee_object *root) {
  struct tee_ioctl_shm_register_fd_data data = {
      .fd = fd,
      .size = 0,
      .flags = 0,
      .id = 0,
  };
  struct adapter_memory *memory;
  struct allocation_record *record;
  struct stat status;
  int shared_memory_fd;

  shared_memory_fd = -1;
  if (fstat(fd, &status) == 0) {
    for (record = allocations; record; record = record->next) {
      if (record->device == status.st_dev && record->inode == status.st_ino) {
        data.id = record->object_id;
        data.size = record->size;
        shared_memory_fd = dup(fd);
        break;
      }
    }
  }
  if (shared_memory_fd < 0) {
    /* The dma-buf and object invocation must share one TEE context. */
    shared_memory_fd =
        ioctl(ROOT_OBJECT(root)->fd, TEE_IOC_SHM_REGISTER_FD, &data);
  }
  /* TZCom_getFdObject borrows this descriptor.  The stock caller retains it
   * in android::base::unique_fd and closes it after the object conversion;
   * consuming it here causes a fdsan-detected double close. */
  if (shared_memory_fd < 0)
    return QCOMTEE_OBJECT_NULL;

  memory = calloc(1, sizeof(*memory));
  if (!memory) {
    close(shared_memory_fd);
    return QCOMTEE_OBJECT_NULL;
  }
  atomic_init(&memory->object.refs, 1);
  memory->object.tee_object_id = (uint64_t)data.id;
  memory->object.object_type = QCOMTEE_OBJECT_TYPE_MEMORY;
  memory->object.root = root;
  memory->object.ops = &memory_operations;
  memory->shared_memory_fd = shared_memory_fd;
  if (qcomtee_object_refs_inc(root) != 0) {
    close(shared_memory_fd);
    free(memory);
    return QCOMTEE_OBJECT_NULL;
  }
  return &memory->object;
}

/* Used only by the companion BufferAllocator ABI shim. */
int luma_qcomtee_allocate(size_t size) {
  struct tee_ioctl_shm_alloc_data data = {
      .size = size,
      .flags = 0,
      .id = 0,
  };
  struct allocation_record *record;
  struct stat status;
  int fd;

  if (!latest_root || size == 0)
    return -1;
  fd = ioctl(ROOT_OBJECT(latest_root)->fd, TEE_IOC_SHM_ALLOC, &data);
  diagnostic_event("shm_alloc", 0, 0, fd < 0 ? -1 : 0,
                   fd < 0 ? errno : (int32_t)data.id);
  if (fd < 0 || fstat(fd, &status) != 0) {
    if (fd >= 0)
      close(fd);
    return -1;
  }
  record = calloc(1, sizeof(*record));
  if (!record) {
    close(fd);
    return -1;
  }
  record->device = status.st_dev;
  record->inode = status.st_ino;
  record->object_id = data.id;
  record->size = data.size;
  record->next = allocations;
  allocations = record;
  return fd;
}

int TZCom_getClientEnvObject(struct object *client_environment) {
  struct qcomtee_object *root;
  struct qcomtee_object *credentials;
  struct qcomtee_param parameters[2] = {0};
  qcomtee_result_t secure_result = QCOMTEE_ERROR;
  int transport_result;

  if (!client_environment)
    return OBJECT_ERROR;
  *client_environment = (struct object){0};
  root = new_root();
  if (root == QCOMTEE_OBJECT_NULL)
    return OBJECT_ERROR;
  credentials = new_credentials(root);
  if (credentials == QCOMTEE_OBJECT_NULL) {
    qcomtee_object_refs_dec(root);
    return OBJECT_ERROR;
  }
  parameters[0].attr = QCOMTEE_OBJREF_INPUT;
  parameters[0].object = credentials;
  parameters[1].attr = QCOMTEE_OBJREF_OUTPUT;
  transport_result =
      qcomtee_object_invoke(root, 2, parameters, 2, &secure_result);
  diagnostic_event("client_environment", 2, 0x1100, transport_result,
                   secure_result);
  if (transport_result != 0) {
    qcomtee_object_refs_dec(credentials);
    qcomtee_object_refs_dec(root);
    return OBJECT_ERROR;
  }
  if (secure_result != QCOMTEE_OK ||
      parameters[1].object == QCOMTEE_OBJECT_NULL) {
    qcomtee_object_refs_dec(root);
    return secure_result ? (int)secure_result : OBJECT_ERROR;
  }
  if (latest_root)
    qcomtee_object_refs_dec(latest_root);
  latest_root = root;
  qcomtee_object_refs_inc(latest_root);
  *client_environment = wrap_object(parameters[1].object);
  qcomtee_object_refs_dec(root);
  if (!client_environment->invoke) {
    qcomtee_object_refs_dec(parameters[1].object);
    return OBJECT_ERROR_KMEM;
  }
  return 0;
}

int TZCom_getFdObject(int fd, struct object *object) {
  struct qcomtee_object *memory;
  if (fd < 0 || !object || !latest_root)
    return OBJECT_ERROR;
  memory = register_dma_buffer(fd, latest_root);
  diagnostic_event("fd_object", 0, 0, memory == QCOMTEE_OBJECT_NULL ? -1 : 0,
                   memory == QCOMTEE_OBJECT_NULL
                       ? errno
                       : (int32_t)memory->tee_object_id);
  if (memory == QCOMTEE_OBJECT_NULL)
    return OBJECT_ERROR;
  *object = wrap_object(memory);
  if (!object->invoke) {
    qcomtee_object_refs_dec(memory);
    return OBJECT_ERROR_KMEM;
  }
  return 0;
}

int MinkDescriptor_shutdownCBService(void) {
  /* Detached callback workers and their root references are process-scoped. */
  return 0;
}
