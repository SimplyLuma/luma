// SPDX-License-Identifier: Apache-2.0
/*
 * SELinux-label bridge for the exact QREL hwservicemanager running inside
 * Luma's disposable stock-IMS namespace.  It permits only the HIDL registry's
 * own interfaces and the two Fairphone IMS interfaces needed by imsdaemon.
 * Binder's real security-context request path is intentionally left intact.
 */

#include <linux/android/binder.h>
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

struct selabel_handle;
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
  return allocate_context(context, "u:r:luma_ims_hwservicemanager:s0");
}
int getpidcon(pid_t pid, char **context) {
  (void)pid;
  return allocate_context(context, "u:r:luma_ims_hidl_client:s0");
}
void freecon(char *context) { free(context); }
struct selabel_handle *selinux_android_hw_service_context_handle(void) {
  return (struct selabel_handle *)&hw_service_context_handle;
}

/* Fedora's Binder LSM does not emit Android SELinux transaction SIDs.  Keep
 * the request flag off and provide the fixed, namespace-local client label to
 * hwservicemanager's own calling-context lookup instead. */
void luma_hidl_set_requesting_sid(const void *binder, int requesting)
    __asm__("_ZN7android8hardware16setRequestingSidERKNS_2spINS_4hidl4base4V1_05IBaseEEEb");
void luma_hidl_set_requesting_sid(const void *binder, int requesting) {
  (void)binder;
  (void)requesting;
}

const char *luma_hidl_get_calling_sid(const void *thread_state)
    __asm__("_ZNK7android8hardware14IPCThreadState13getCallingSidEv");
const char *luma_hidl_get_calling_sid(const void *thread_state) {
  (void)thread_state;
  return "u:r:luma_ims_hidl_client:s0";
}

int ioctl(int descriptor, unsigned long request, ...) {
  va_list arguments;
  void *argument;
  va_start(arguments, request);
  argument = va_arg(arguments, void *);
  va_end(arguments);
  if (request == BINDER_SET_CONTEXT_MGR_EXT && argument) {
    struct flat_binder_object *manager = argument;
    manager->flags &= ~FLAT_BINDER_FLAG_TXN_SECURITY_CTX;
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
  if (!same_string(target_class, "hwservice_manager")) {
    dprintf(STDERR_FILENO,
            "event=luma_hidl_policy_check class=other permission=denied\n");
    return -1;
  }
  if (same_string(permission, "add") || same_string(permission, "find") ||
      same_string(permission, "list") || same_string(permission, "get")) {
    dprintf(STDERR_FILENO,
            "event=luma_hidl_policy_check class=hwservice_manager permission=%s result=allowed\n",
            permission);
    return 0;
  }
  dprintf(STDERR_FILENO,
          "event=luma_hidl_policy_check class=hwservice_manager permission=%s result=denied\n",
          permission ? permission : "none");
  return -1;
}

static const char *service_class(const char *name) {
  if (same_string(name, "android.hidl.base::IBase"))
    return "hidl_base";
  if (same_string(name, "android.hidl.manager::IServiceManager"))
    return "hidl_manager";
  if (same_string(name, "android.hidl.token::ITokenManager"))
    return "hidl_token";
  if (same_string(name, "vendor.qti.ims.callinfo::IService"))
    return "ims_callinfo";
  if (same_string(name, "vendor.qti.ims.factory::IImsFactory"))
    return "ims_factory";
  return "other";
}

int selabel_lookup(struct selabel_handle *handle, char **context,
                   const char *key, int type) {
  static const char registry_context[] = "u:object_r:luma_ims_hwservice:s0";
  const char *classification;
  char *copy;
  (void)handle;
  (void)type;
  classification = service_class(key);
  dprintf(STDERR_FILENO,
          "event=luma_hidl_policy_lookup interface=%s result=%s\n",
          classification, same_string(classification, "other") ? "denied" : "allowed");
  if (!context || same_string(classification, "other"))
    return -1;
  copy = malloc(sizeof(registry_context));
  if (!copy)
    return -1;
  for (size_t index = 0; index < sizeof(registry_context); ++index)
    copy[index] = registry_context[index];
  *context = copy;
  return 0;
}
