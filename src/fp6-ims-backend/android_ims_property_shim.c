// SPDX-License-Identifier: Apache-2.0
/*
 * Fixed property view for the unmodified QREL 16.95.0 IMS daemon when it is
 * run in Luma's disposable Android service namespace.  These values describe
 * the stock FP6 release and do not touch Android's persistent property area.
 */

#include <dlfcn.h>
#include <stddef.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>

struct prop_info {
  unsigned char opaque;
};

typedef void (*property_read_callback)(void *cookie, const char *name,
                                      const char *value, uint32_t serial);

struct fixed_property {
  const char *name;
  const char *value;
  struct prop_info info;
};

static struct fixed_property properties[] = {
    {"ro.arch", "arm64", {0}},
    {"ro.board.platform", "volcano", {0}},
    {"ro.boot.product.vendor.sku", "volcano", {0}},
    {"ro.build.version.codename", "REL", {0}},
    {"ro.build.version.release", "16", {0}},
    {"ro.build.version.sdk", "36", {0}},
    {"ro.debuggable", "0", {0}},
    {"hwservicemanager.ready", "true", {0}},
    {"servicemanager.ready", "true", {0}},
    {"persist.vendor.rcs.singlereg.feature", "1", {0}},
    {"vendor.ims.DATA_DAEMON_STATUS", "1", {0}},
    {"vendor.ims.ENABLE_HELPER", "0", {0}},
    {"vendor.ims.logging", "0", {0}},
    {"vendor.ims.modem.multisub.cap", "2", {0}},
    {"vendor.ims.modemssr", "0", {0}},
};

static int same_string(const char *left, const char *right) {
  size_t index = 0;
  if (!left || !right)
    return 0;
  while (left[index] && right[index] && left[index] == right[index])
    ++index;
  return left[index] == '\0' && right[index] == '\0';
}

static struct fixed_property *find_name(const char *name) {
  size_t index;
  for (index = 0; index < sizeof(properties) / sizeof(properties[0]); ++index) {
    if (same_string(name, properties[index].name))
      return &properties[index];
  }
  return NULL;
}

static struct fixed_property *find_info(const struct prop_info *info) {
  size_t index;
  for (index = 0; index < sizeof(properties) / sizeof(properties[0]); ++index) {
    if (info == &properties[index].info)
      return &properties[index];
  }
  return NULL;
}

int __system_property_get(const char *name, char *value) {
  struct fixed_property *property = find_name(name);
  size_t length;
  if (!property || !value)
    return 0;
  for (length = 0; property->value[length]; ++length)
    value[length] = property->value[length];
  value[length] = '\0';
  return (int)length;
}

const struct prop_info *__system_property_find(const char *name) {
  struct fixed_property *property = find_name(name);
  return property ? &property->info : NULL;
}

void __system_property_read_callback(const struct prop_info *info,
                                     property_read_callback callback,
                                     void *cookie) {
  struct fixed_property *property = find_info(info);
  if (property && callback)
    callback(cookie, property->name, property->value, 1);
}

uint32_t __system_property_serial(const struct prop_info *info) {
  return find_info(info) ? 1 : 0;
}

int32_t AServiceManager_addService(void *binder, const char *instance) {
  typedef int32_t (*add_service_fn)(void *, const char *);
  static add_service_fn real_add_service;
  int32_t result;
  if (!real_add_service)
    real_add_service =
        (add_service_fn)dlsym(RTLD_NEXT, "AServiceManager_addService");
  if (!real_add_service)
    return -38;
  result = real_add_service(binder, instance);
  dprintf(STDERR_FILENO,
          "event=luma_ims_binder_add_service instance=%s status=%d\n",
          instance ? instance : "(null)", result);
  return result;
}

void android_set_abort_message(const char *message) {
  typedef void (*set_abort_message_fn)(const char *);
  static set_abort_message_fn real_set_abort_message;
  dprintf(STDERR_FILENO, "event=luma_ims_abort_message detail=%s\n",
          message ? message : "(null)");
  if (!real_set_abort_message)
    real_set_abort_message =
        (set_abort_message_fn)dlsym(RTLD_NEXT, "android_set_abort_message");
  if (real_set_abort_message)
    real_set_abort_message(message);
}

/* The stock daemon keeps early diagnostics in an in-memory ring which is
 * normally drained by Android's crash/log service. Mirror those startup lines
 * to this disposable process' private stderr so Luma can diagnose the gate. */
void luma_ims_print_log(int level, const char *source, int line,
                        const char *format, ...)
    __asm__("_ZN6ImsLog8printLogEiPKciS1_z");
void luma_ims_print_log(int level, const char *source, int line,
                        const char *format, ...) {
  va_list arguments;
  dprintf(STDERR_FILENO, "stock-ims level=%d source=%s line=%d message=",
          level, source ? source : "(null)", line);
  va_start(arguments, format);
  vdprintf(STDERR_FILENO, format ? format : "(null)", arguments);
  va_end(arguments);
  dprintf(STDERR_FILENO, "\n");
}
