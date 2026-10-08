/* SPDX-License-Identifier: Apache-2.0 */

#ifndef LUMA_FP6_FOCAL_DRIVER_CONTROL_H
#define LUMA_FP6_FOCAL_DRIVER_CONTROL_H

#include <stddef.h>
#include <stdint.h>

#define FOCAL_DRIVER_VERSION_SIZE 32u
#define FOCAL_DRIVER_CONFIG_SIZE 88u
#define FOCAL_DRIVER_FEATURE_SIZE 136u

/* Exact Linux UAPI values exported by the FP6 FocalTech shim. */
#define FOCAL_IOCTL_INIT_DRIVER UINT32_C(0x00006600)
#define FOCAL_IOCTL_FREE_DRIVER UINT32_C(0x00006601)
#define FOCAL_IOCTL_RESET_DEVICE_HL UINT32_C(0x40046602)
#define FOCAL_IOCTL_ENABLE_IRQ UINT32_C(0x00006603)
#define FOCAL_IOCTL_DISABLE_IRQ UINT32_C(0x00006604)
#define FOCAL_IOCTL_ENABLE_POWER UINT32_C(0x00006607)
#define FOCAL_IOCTL_DISABLE_POWER UINT32_C(0x00006608)
#define FOCAL_IOCTL_SYNC_CONFIG UINT32_C(0xc058660a)
#define FOCAL_IOCTL_GET_VERSION UINT32_C(0x8001660b)
#define FOCAL_IOCTL_SET_VENDOR_INFO UINT32_C(0x4001660c)
#define FOCAL_IOCTL_GET_FEATURE UINT32_C(0x80886611)
#define FOCAL_IOCTL_SET_EVENT_TYPE UINT32_C(0x40046612)
#define FOCAL_IOCTL_GET_EVENT_INFO UINT32_C(0x80046613)

struct focal_driver_config {
	uint8_t enable_fasync;
	uint8_t reserved0[3];
	int32_t gesture_keycode[8];
	uint8_t enable_spidev;
	uint8_t reserved1[3];
	int32_t spidev_bus;
	int32_t spidev_chip_select;
	int32_t gpio_mosi;
	int32_t gpio_miso;
	int32_t gpio_clock;
	int32_t gpio_chip_select;
	int32_t gpio_reset;
	int32_t gpio_interrupt;
	int32_t gpio_power;
	int32_t gpio_iovcc;
	int32_t log_level;
	uint8_t logcat_driver;
	uint8_t reserved2[3];
};

struct focal_driver_feature {
	uint16_t version;
	uint16_t reserved0;
	uint32_t flags;
	uint32_t reserved1[32];
};

typedef int (*focal_driver_ioctl_fn)(void *context, uint32_t request,
				     uintptr_t argument);
typedef int (*focal_driver_sleep_fn)(void *context, unsigned int milliseconds);

struct focal_driver_transport {
	focal_driver_ioctl_fn ioctl;
	focal_driver_sleep_fn sleep_ms;
	void *context;
};

void focal_driver_stock_config(struct focal_driver_config *config);
int focal_driver_prepare(const struct focal_driver_transport *transport,
			 struct focal_driver_feature *feature,
			 char version[FOCAL_DRIVER_VERSION_SIZE]);
int focal_driver_prepare_probe(const struct focal_driver_transport *transport);
int focal_driver_retry_probe(const struct focal_driver_transport *transport);
int focal_driver_finish_probe(const struct focal_driver_transport *transport);
int focal_driver_prepare_capture(const struct focal_driver_transport *transport);
int focal_driver_cleanup(const struct focal_driver_transport *transport);

#endif
