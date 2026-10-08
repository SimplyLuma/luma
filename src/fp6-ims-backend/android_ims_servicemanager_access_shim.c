// SPDX-License-Identifier: Apache-2.0
/*
 * Narrow SELinux/Binder registry bridge for Luma's disposable stock-IMS
 * namespace.  Fedora has no Android service_manager policy, so the isolated
 * manager permits only its own registry and Fairphone's IMS factory service.
 */

#include <linux/android/binder.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

extern int *__errno(void);

struct selabel_handle;
static unsigned char service_context_handle;
static unsigned char hw_service_context_handle;

static int same_string(const char *left, const char *right) {
  size_t index = 0;
  if (!left || !right)
    return 0;
  while (left[index] && right[index] && left[index] == right[index])
    ++index;
  return left[index] == '\0' && right[index] == '\0';
}

static int allocate_context(char **context, const char *value) {
  size_t length = 0;
  char *copy;
  if (!context || !value)
    return -1;
  while (value[length])
    ++length;
  copy = malloc(length + 1);
  if (!copy)
    return -1;
  for (size_t index = 0; index <= length; ++index)
    copy[index] = value[index];
  *context = copy;
  return 0;
}

int selinux_status_open(int fallback) {
  (void)fallback;
  return 0;
}
int selinux_status_updated(void) { return 0; }
int getcon(char **context) {
  return allocate_context(context, "u:r:luma_ims_servicemanager:s0");
}
int getpidcon(pid_t pid, char **context) {
  (void)pid;
  return allocate_context(context, "u:r:luma_ims_client:s0");
}
void freecon(char *context) { free(context); }
struct selabel_handle *selinux_android_service_context_handle(void) {
  return (struct selabel_handle *)&service_context_handle;
}
struct selabel_handle *selinux_android_hw_service_context_handle(void) {
  return (struct selabel_handle *)&hw_service_context_handle;
}

void luma_set_requesting_sid(void *binder, int requesting)
    __asm__("_ZN7android7BBinder16setRequestingSidEb");
void luma_set_requesting_sid(void *binder, int requesting) {
  (void)binder;
  (void)requesting;
}

void luma_hidl_set_requesting_sid(const void *binder, int requesting)
    __asm__("_ZN7android8hardware16setRequestingSidERKNS_2spINS_4hidl4base4V1_05IBaseEEEb");
void luma_hidl_set_requesting_sid(const void *binder, int requesting) {
  (void)binder;
  (void)requesting;
}

const char *luma_get_calling_sid(const void *thread_state)
    __asm__("_ZNK7android14IPCThreadState13getCallingSidEv");
const char *luma_get_calling_sid(const void *thread_state) {
  (void)thread_state;
  return "u:r:luma_ims_client:s0";
}

int ioctl(int descriptor, unsigned long request, ...) {
  va_list arguments;
  void *argument;
  va_start(arguments, request);
  argument = va_arg(arguments, void *);
  va_end(arguments);
  if (request == BINDER_SET_CONTEXT_MGR_EXT) {
    *__errno() = 22;
    return -1;
  }
  return (int)syscall(SYS_ioctl, descriptor, request, argument);
}

int selinux_check_access(const char *source_context,
                         const char *target_context,
                         const char *target_class, const char *permission,
                         void *audit_data) {
  (void)source_context;
  (void)target_context;
  (void)audit_data;
  if (!same_string(target_class, "service_manager") &&
      !same_string(target_class, "hwservice_manager"))
    return -1;
  if (same_string(permission, "add") || same_string(permission, "find") ||
      same_string(permission, "list") || same_string(permission, "get"))
    return 0;
  return -1;
}

static int allowed_service(const char *name) {
  return same_string(name, "manager") ||
         same_string(name,
                     "vendor.qti.ims.factoryaidlservice.IImsFactory/default") ||
         same_string(name, "vendor.qti.ims.callinfo::IService") ||
         same_string(name, "vendor.qti.ims.factory::IImsFactory");
}

int selabel_lookup(struct selabel_handle *handle, char **context,
                   const char *key, int type) {
  static const char registry_context[] = "u:object_r:luma_ims_service:s0";
  char *copy;
  (void)handle;
  (void)type;
  if (!context || !allowed_service(key))
    return -1;
  copy = malloc(sizeof(registry_context));
  if (!copy)
    return -1;
  for (size_t index = 0; index < sizeof(registry_context); ++index)
    copy[index] = registry_context[index];
  *context = copy;
  return 0;
}
