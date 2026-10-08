// SPDX-License-Identifier: Apache-2.0
/*
 * One-property bridge for running the unmodified QREL biometric services in
 * Luma's disposable Android service namespace.  The caller must independently
 * verify that listeners 10, 8192, and 28672 are active before loading it.
 */

#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>

struct prop_info {
  unsigned char opaque;
};

typedef void (*property_read_callback)(void *cookie, const char *name,
                                      const char *value, uint32_t serial);

static const char readiness_name[] = "vendor.sys.listeners.registered";
static const char readiness_value[] = "true";
static const char security_patch_name[] = "ro.build.version.security_patch";
static const char security_patch_value[] = "2026-07-05";
static const char release_name[] = "ro.build.version.release";
static const char release_value[] = "16";
static const char vendor_patch_name[] = "ro.vendor.build.security_patch";
static const char vendor_patch_value[] = "2026-07-05";
static const char gatekeeper_level_name[] =
    "vendor.gatekeeper.is_security_level_spu";
static const char gatekeeper_level_value[] = "0";
static const char sdk_name[] = "ro.build.version.sdk";
static const char sdk_value[] = "36";
static const char codename_name[] = "ro.build.version.codename";
static const char codename_value[] = "REL";
static const char architecture_name[] = "ro.arch";
static const char architecture_value[] = "arm64";
static const char servicemanager_ready_name[] = "servicemanager.ready";
static const char servicemanager_ready_value[] = "true";
static const char vendor_sku_name[] = "ro.boot.product.vendor.sku";
static const char vendor_sku_value[] = "volcano";
static const char hardware_sku_name[] = "ro.boot.product.hardware.sku";
static const char hardware_sku_value[] = "non_qmaa";
static const char fdsan_name[] = "debug.fdsan";
static const char fdsan_value[] = "0";
static const struct prop_info readiness_property = {0};
static const struct prop_info security_patch_property = {0};
static const struct prop_info release_property = {0};
static const struct prop_info vendor_patch_property = {0};
static const struct prop_info gatekeeper_level_property = {0};
static const struct prop_info sdk_property = {0};
static const struct prop_info codename_property = {0};
static const struct prop_info architecture_property = {0};
static const struct prop_info servicemanager_ready_property = {0};
static const struct prop_info vendor_sku_property = {0};
static const struct prop_info hardware_sku_property = {0};
static const struct prop_info fdsan_property = {0};

static int same_string(const char *left, const char *right) {
  size_t index = 0;
  if (!left || !right)
    return 0;
  while (left[index] && right[index] && left[index] == right[index])
    ++index;
  return left[index] == '\0' && right[index] == '\0';
}

int __system_property_get(const char *name, char *value) {
  const char *property_value;
  size_t length;
  if (!value)
    return 0;
  if (same_string(name, readiness_name))
    property_value = readiness_value;
  else if (same_string(name, security_patch_name))
    property_value = security_patch_value;
  else if (same_string(name, release_name))
    property_value = release_value;
  else if (same_string(name, vendor_patch_name))
    property_value = vendor_patch_value;
  else if (same_string(name, gatekeeper_level_name))
    property_value = gatekeeper_level_value;
  else if (same_string(name, sdk_name))
    property_value = sdk_value;
  else if (same_string(name, codename_name))
    property_value = codename_value;
  else if (same_string(name, architecture_name))
    property_value = architecture_value;
  else if (same_string(name, servicemanager_ready_name))
    property_value = servicemanager_ready_value;
  else if (same_string(name, vendor_sku_name))
    property_value = vendor_sku_value;
  else if (same_string(name, hardware_sku_name))
    property_value = hardware_sku_value;
  else if (same_string(name, fdsan_name))
    property_value = fdsan_value;
  else
    return 0;
  for (length = 0; property_value[length]; ++length)
    value[length] = property_value[length];
  value[length] = '\0';
  return (int)length;
}

const struct prop_info *__system_property_find(const char *name) {
  if (same_string(name, readiness_name))
    return &readiness_property;
  if (same_string(name, security_patch_name))
    return &security_patch_property;
  if (same_string(name, release_name))
    return &release_property;
  if (same_string(name, vendor_patch_name))
    return &vendor_patch_property;
  if (same_string(name, gatekeeper_level_name))
    return &gatekeeper_level_property;
  if (same_string(name, sdk_name))
    return &sdk_property;
  if (same_string(name, codename_name))
    return &codename_property;
  if (same_string(name, architecture_name))
    return &architecture_property;
  if (same_string(name, servicemanager_ready_name))
    return &servicemanager_ready_property;
  if (same_string(name, vendor_sku_name))
    return &vendor_sku_property;
  if (same_string(name, hardware_sku_name))
    return &hardware_sku_property;
  if (same_string(name, fdsan_name))
    return &fdsan_property;
  return NULL;
}

void __system_property_read_callback(const struct prop_info *property,
                                     property_read_callback callback,
                                     void *cookie) {
  if (property == &readiness_property && callback)
    callback(cookie, readiness_name, readiness_value, 1);
  else if (property == &security_patch_property && callback)
    callback(cookie, security_patch_name, security_patch_value, 1);
  else if (property == &release_property && callback)
    callback(cookie, release_name, release_value, 1);
  else if (property == &vendor_patch_property && callback)
    callback(cookie, vendor_patch_name, vendor_patch_value, 1);
  else if (property == &gatekeeper_level_property && callback)
    callback(cookie, gatekeeper_level_name, gatekeeper_level_value, 1);
  else if (property == &sdk_property && callback)
    callback(cookie, sdk_name, sdk_value, 1);
  else if (property == &codename_property && callback)
    callback(cookie, codename_name, codename_value, 1);
  else if (property == &architecture_property && callback)
    callback(cookie, architecture_name, architecture_value, 1);
  else if (property == &servicemanager_ready_property && callback)
    callback(cookie, servicemanager_ready_name, servicemanager_ready_value, 1);
  else if (property == &vendor_sku_property && callback)
    callback(cookie, vendor_sku_name, vendor_sku_value, 1);
  else if (property == &hardware_sku_property && callback)
    callback(cookie, hardware_sku_name, hardware_sku_value, 1);
  else if (property == &fdsan_property && callback)
    callback(cookie, fdsan_name, fdsan_value, 1);
}

uint32_t __system_property_serial(const struct prop_info *property) {
  return property == &readiness_property ||
                 property == &security_patch_property ||
                 property == &release_property ||
                 property == &vendor_patch_property ||
                 property == &gatekeeper_level_property ||
                 property == &sdk_property || property == &codename_property ||
                 property == &architecture_property ||
                 property == &servicemanager_ready_property ||
                 property == &vendor_sku_property ||
                 property == &hardware_sku_property ||
                 property == &fdsan_property
             ? 1
             : 0;
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
          "event=luma_binder_add_service instance=%s status=%d\n",
          instance ? instance : "(null)", result);
  return result;
}
