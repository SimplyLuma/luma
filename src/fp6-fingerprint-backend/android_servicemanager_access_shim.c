// SPDX-License-Identifier: Apache-2.0
/*
 * Narrow access bridge for Luma's disposable Android Binder namespace.
 * Fedora does not carry Android's SELinux policy, so Android servicemanager
 * cannot evaluate its service_manager class.  Permit only the three Binder
 * registry operations used in the isolated namespace; deny everything else.
 */

#include <stddef.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <sys/types.h>

#include <linux/android/binder.h>

/* Bionic exposes errno through __errno(), unlike glibc's headers. */
extern int *__errno(void);

struct selabel_handle;
static unsigned char service_context_handle;

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
  return allocate_context(context, "u:r:luma_biometric_servicemanager:s0");
}

int getpidcon(pid_t pid, char **context) {
  (void)pid;
  return allocate_context(context, "u:r:luma_biometric_client:s0");
}

void freecon(char *context) { free(context); }

struct selabel_handle *selinux_android_service_context_handle(void) {
  return (struct selabel_handle *)&service_context_handle;
}

void luma_set_requesting_sid(void *binder, int requesting)
    __asm__("_ZN7android7BBinder16setRequestingSidEb");
void luma_set_requesting_sid(void *binder, int requesting) {
  (void)binder;
  (void)requesting;
  dprintf(STDERR_FILENO,
          "event=luma_servicemanager_requesting_sid enabled=false\n");
}

const char *luma_get_calling_sid(const void *thread_state)
    __asm__("_ZNK7android14IPCThreadState13getCallingSidEv");
const char *luma_get_calling_sid(const void *thread_state) {
  (void)thread_state;
  /* Access checks are still enforced by the service-name allowlist below. */
  return "u:r:luma_biometric_client:s0";
}

/*
 * Android's current ProcessState registers the context manager with the EXT
 * ioctl and a flat_binder_object that asks Binder for transaction security
 * contexts.  Mainline Fedora deliberately has no Android SELinux security
 * context provider, so the kernel rejects every otherwise-valid transaction
 * with -EOPNOTSUPP before servicemanager can inspect it.  This library is
 * loaded only into Luma's disposable, private servicemanager.  Register that
 * one manager with the legacy ABI, which provides the same context-manager
 * routing without requesting Android transaction labels.
 */
int ioctl(int descriptor, unsigned long request, ...) {
  va_list arguments;
  void *argument;

  va_start(arguments, request);
  argument = va_arg(arguments, void *);
  va_end(arguments);

  if (request == BINDER_SET_CONTEXT_MGR_EXT) {
    /* Let libbinder execute its own legacy fallback and bookkeeping. */
    *__errno() = 22; /* EINVAL */
    dprintf(STDERR_FILENO,
            "event=luma_servicemanager_context_manager abi=extended result=-1 fallback=legacy\n");
    return -1;
  }

  return (int)syscall(SYS_ioctl, descriptor, request, argument);
}

static int same_string(const char *left, const char *right) {
  size_t index = 0;
  if (!left || !right)
    return 0;
  while (left[index] && right[index] && left[index] == right[index])
    ++index;
  return left[index] == '\0' && right[index] == '\0';
}

int selinux_check_access(const char *source_context,
                         const char *target_context,
                         const char *target_class, const char *permission,
                         void *audit_data) {
  (void)source_context;
  (void)target_context;
  (void)audit_data;
  dprintf(STDERR_FILENO,
          "event=luma_servicemanager_access class=%s permission=%s\n",
          target_class ? target_class : "(null)",
          permission ? permission : "(null)");
  if (!same_string(target_class, "service_manager"))
    return -1;
  if (same_string(permission, "add") || same_string(permission, "find") ||
      same_string(permission, "list"))
    return 0;
  return -1;
}

static int allowed_service(const char *name) {
  return same_string(name, "manager") ||
         same_string(
             name,
             "android.hardware.security.keymint.IKeyMintDevice/default") ||
         same_string(name,
                     "android.hardware.security.secureclock.ISecureClock/default") ||
         same_string(name,
                     "android.hardware.security.sharedsecret.ISharedSecret/default") ||
         same_string(name,
                     "android.hardware.security.keymint.IRemotelyProvisionedComponent/default") ||
         same_string(name,
                     "android.hardware.gatekeeper.IGatekeeper/default") ||
         same_string(name, "FocalFingerprintService") ||
         same_string(name,
                     "android.hardware.biometrics.fingerprint.IFingerprint/default");
}

int selabel_lookup(struct selabel_handle *handle, char **context,
                   const char *key, int type) {
  static const char registry_context[] =
      "u:object_r:luma_biometric_service:s0";
  char *copy;
  (void)handle;
  (void)type;
  dprintf(STDERR_FILENO, "event=luma_servicemanager_lookup service=%s\n",
          key ? key : "(null)");
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
