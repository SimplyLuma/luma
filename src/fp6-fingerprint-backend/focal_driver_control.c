/* SPDX-License-Identifier: Apache-2.0 */

#include "focal_driver_control.h"

#include <string.h>

_Static_assert(sizeof(struct focal_driver_config) == FOCAL_DRIVER_CONFIG_SIZE,
	       "FocalTech driver configuration ABI changed");
_Static_assert(sizeof(struct focal_driver_feature) == FOCAL_DRIVER_FEATURE_SIZE,
	       "FocalTech driver feature ABI changed");

static int control(const struct focal_driver_transport *transport,
		   uint32_t request, uintptr_t argument)
{
	if (!transport || !transport->ioctl)
		return -1;
	return transport->ioctl(transport->context, request, argument);
}

static int sleep_ms(const struct focal_driver_transport *transport,
		    unsigned int milliseconds)
{
	if (!transport || !transport->sleep_ms)
		return -1;
	return transport->sleep_ms(transport->context, milliseconds);
}

void focal_driver_stock_config(struct focal_driver_config *config)
{
	static const int32_t stock_gestures[8] = {
		0, 0, 0, 0, 28, 158, 0, 584,
	};

	if (!config)
		return;
	memset(config, 0, sizeof(*config));
	memcpy(config->gesture_keycode, stock_gestures, sizeof(stock_gestures));
	config->spidev_bus = 2;
	config->spidev_chip_select = 0;
	config->gpio_mosi = -1;
	config->gpio_miso = -1;
	config->gpio_clock = -1;
	config->gpio_chip_select = -1;
	config->gpio_reset = -1;
	config->gpio_interrupt = -1;
	config->gpio_power = -1;
	config->gpio_iovcc = -1;
	config->log_level = 2;
}

int focal_driver_prepare(const struct focal_driver_transport *transport,
			 struct focal_driver_feature *feature,
			 char version[FOCAL_DRIVER_VERSION_SIZE])
{
	struct focal_driver_config config;
	uint32_t event_type = 3;
	int result;

	if (!feature || !version)
		return -1;
	focal_driver_stock_config(&config);
	memset(feature, 0, sizeof(*feature));
	memset(version, 0, FOCAL_DRIVER_VERSION_SIZE);

	result = control(transport, FOCAL_IOCTL_SYNC_CONFIG,
			 (uintptr_t)&config);
	if (result)
		return result;
	result = control(transport, FOCAL_IOCTL_INIT_DRIVER, 0);
	if (result)
		return result;
	result = control(transport, FOCAL_IOCTL_GET_FEATURE,
			 (uintptr_t)feature);
	if (result)
		return result;
	result = control(transport, FOCAL_IOCTL_SET_EVENT_TYPE,
			 (uintptr_t)&event_type);
	if (result)
		return result;
	return control(transport, FOCAL_IOCTL_GET_VERSION, (uintptr_t)version);
}

int focal_driver_prepare_probe(const struct focal_driver_transport *transport)
{
	int result;

	result = control(transport, FOCAL_IOCTL_DISABLE_IRQ, 0);
	if (result)
		return result;
	result = control(transport, FOCAL_IOCTL_RESET_DEVICE_HL, 0);
	if (result)
		return result;
	result = sleep_ms(transport, 5);
	if (result)
		return result;
	result = control(transport, FOCAL_IOCTL_ENABLE_POWER, 0);
	if (result)
		return result;
	result = sleep_ms(transport, 10);
	if (result)
		return result;
	result = control(transport, FOCAL_IOCTL_RESET_DEVICE_HL, 1);
	if (result)
		return result;
	result = sleep_ms(transport, 10);
	if (result)
		return result;

	/* Stock's default re-power-before-probe path performs one more reset. */
	result = control(transport, FOCAL_IOCTL_RESET_DEVICE_HL, 0);
	if (result)
		return result;
	result = sleep_ms(transport, 10);
	if (result)
		return result;
	result = control(transport, FOCAL_IOCTL_RESET_DEVICE_HL, 1);
	if (result)
		return result;
	return sleep_ms(transport, 10);
}

int focal_driver_retry_probe(const struct focal_driver_transport *transport)
{
	int result;

	result = control(transport, FOCAL_IOCTL_DISABLE_POWER, 0);
	if (result)
		return result;
	result = sleep_ms(transport, 100);
	if (result)
		return result;
	result = control(transport, FOCAL_IOCTL_ENABLE_POWER, 0);
	if (result)
		return result;
	return sleep_ms(transport, 10);
}

int focal_driver_finish_probe(const struct focal_driver_transport *transport)
{
	static const char vendor[] = "focaltech";
	int event = 0;
	int result;

	result = control(transport, FOCAL_IOCTL_SET_VENDOR_INFO,
			 (uintptr_t)vendor);
	if (result)
		return result;
	result = control(transport, FOCAL_IOCTL_GET_EVENT_INFO,
			 (uintptr_t)&event);
	if (result)
		return result;
	return control(transport, FOCAL_IOCTL_ENABLE_IRQ, 0);
}

int focal_driver_prepare_capture(const struct focal_driver_transport *transport)
{
	int result;

	/*
	 * QREL 16.95.0's enable_prev_hw_process path calls
	 * ff_device_hw_reset(false) immediately before CAPTURE_IMAGE.  With the
	 * false argument it performs only this reset pulse; it does not create an
	 * alternate IRQ context or expose sensor data to normal world.
	 */
	result = control(transport, FOCAL_IOCTL_RESET_DEVICE_HL, 0);
	if (result)
		return result;
	result = sleep_ms(transport, 10);
	if (result)
		return result;
	result = control(transport, FOCAL_IOCTL_RESET_DEVICE_HL, 1);
	if (result)
		return result;
	return sleep_ms(transport, 10);
}

int focal_driver_cleanup(const struct focal_driver_transport *transport)
{
	int first_error = 0;
	int result;

	result = control(transport, FOCAL_IOCTL_DISABLE_IRQ, 0);
	if (result && !first_error)
		first_error = result;
	result = control(transport, FOCAL_IOCTL_DISABLE_POWER, 0);
	if (result && !first_error)
		first_error = result;
	result = control(transport, FOCAL_IOCTL_FREE_DRIVER, 0);
	if (result && !first_error)
		first_error = result;
	return first_error;
}
