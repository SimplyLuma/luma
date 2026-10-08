/* SPDX-License-Identifier: Apache-2.0 */

#include "focal_driver_control.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct operation {
	uint32_t request;
	uintptr_t argument;
	unsigned int milliseconds;
	int is_sleep;
};

struct fake_transport {
	struct operation operations[32];
	size_t count;
};

static void fail(const char *expression, const char *file, int line)
{
	fprintf(stderr, "FAIL: %s (%s:%d)\n", expression, file, line);
	exit(1);
}

#define CHECK(expression) \
	do { if (!(expression)) fail(#expression, __FILE__, __LINE__); } while (0)

static int fake_ioctl(void *opaque, uint32_t request, uintptr_t argument)
{
	struct fake_transport *transport = opaque;
	struct operation *operation = &transport->operations[transport->count++];

	operation->request = request;
	operation->argument = argument;
	if (request == FOCAL_IOCTL_GET_FEATURE) {
		struct focal_driver_feature *feature = (void *)argument;
		feature->version = 0x0102;
	}
	if (request == FOCAL_IOCTL_GET_VERSION)
		memcpy((void *)argument, "v2.1-test", sizeof("v2.1-test"));
	return 0;
}

static int fake_sleep(void *opaque, unsigned int milliseconds)
{
	struct fake_transport *transport = opaque;
	struct operation *operation = &transport->operations[transport->count++];

	operation->is_sleep = 1;
	operation->milliseconds = milliseconds;
	return 0;
}

static struct focal_driver_transport transport_for(struct fake_transport *fake)
{
	const struct focal_driver_transport transport = {
		.ioctl = fake_ioctl,
		.sleep_ms = fake_sleep,
		.context = fake,
	};

	return transport;
}

static void test_stock_config(void)
{
	static const uint8_t expected[FOCAL_DRIVER_CONFIG_SIZE] = {
		0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
		0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
		0x00, 0x00, 0x00, 0x00, 0x1c, 0x00, 0x00, 0x00,
		0x9e, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
		0x48, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
		0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
		0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
		0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
		0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
		0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
		0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
	};
	struct focal_driver_config config;

	focal_driver_stock_config(&config);
	CHECK(sizeof(config) == 88);
	CHECK(config.enable_fasync == 0);
	CHECK(config.gesture_keycode[4] == 28);
	CHECK(config.gesture_keycode[5] == 158);
	CHECK(config.gesture_keycode[7] == 584);
	CHECK(config.spidev_bus == 2);
	CHECK(config.gpio_mosi == -1);
	CHECK(config.gpio_iovcc == -1);
	CHECK(config.log_level == 2);
	CHECK(!memcmp(&config, expected, sizeof(expected)));
}

static void test_stock_sequence(void)
{
	struct fake_transport fake = {};
	struct focal_driver_transport transport = transport_for(&fake);
	struct focal_driver_feature feature;
	char version[FOCAL_DRIVER_VERSION_SIZE];
	const uint32_t prepare[] = {
		FOCAL_IOCTL_SYNC_CONFIG,
		FOCAL_IOCTL_INIT_DRIVER,
		FOCAL_IOCTL_GET_FEATURE,
		FOCAL_IOCTL_SET_EVENT_TYPE,
		FOCAL_IOCTL_GET_VERSION,
	};
	const uint32_t finish[] = {
		FOCAL_IOCTL_SET_VENDOR_INFO,
		FOCAL_IOCTL_GET_EVENT_INFO,
		FOCAL_IOCTL_ENABLE_IRQ,
	};
	const uint32_t cleanup[] = {
		FOCAL_IOCTL_DISABLE_IRQ,
		FOCAL_IOCTL_DISABLE_POWER,
		FOCAL_IOCTL_FREE_DRIVER,
	};

	CHECK(focal_driver_prepare(&transport, &feature, version) == 0);
	CHECK(fake.count == sizeof(prepare) / sizeof(prepare[0]));
	for (size_t index = 0; index < fake.count; index++)
		CHECK(fake.operations[index].request == prepare[index]);
	CHECK(feature.version == 0x0102);
	CHECK(!strcmp(version, "v2.1-test"));

	fake.count = 0;
	CHECK(focal_driver_prepare_probe(&transport) == 0);
	CHECK(fake.count == 11);
	CHECK(fake.operations[0].request == FOCAL_IOCTL_DISABLE_IRQ);
	CHECK(fake.operations[1].request == FOCAL_IOCTL_RESET_DEVICE_HL);
	CHECK(fake.operations[1].argument == 0);
	CHECK(fake.operations[2].is_sleep && fake.operations[2].milliseconds == 5);
	CHECK(fake.operations[3].request == FOCAL_IOCTL_ENABLE_POWER);
	CHECK(fake.operations[4].is_sleep && fake.operations[4].milliseconds == 10);
	CHECK(fake.operations[5].request == FOCAL_IOCTL_RESET_DEVICE_HL);
	CHECK(fake.operations[5].argument == 1);
	CHECK(fake.operations[7].request == FOCAL_IOCTL_RESET_DEVICE_HL);
	CHECK(fake.operations[7].argument == 0);
	CHECK(fake.operations[9].request == FOCAL_IOCTL_RESET_DEVICE_HL);
	CHECK(fake.operations[9].argument == 1);

	fake.count = 0;
	CHECK(focal_driver_retry_probe(&transport) == 0);
	CHECK(fake.count == 4);
	CHECK(fake.operations[0].request == FOCAL_IOCTL_DISABLE_POWER);
	CHECK(fake.operations[1].is_sleep && fake.operations[1].milliseconds == 100);
	CHECK(fake.operations[2].request == FOCAL_IOCTL_ENABLE_POWER);
	CHECK(fake.operations[3].is_sleep && fake.operations[3].milliseconds == 10);

	fake.count = 0;
	CHECK(focal_driver_finish_probe(&transport) == 0);
	for (size_t index = 0; index < fake.count; index++)
		CHECK(fake.operations[index].request == finish[index]);

	fake.count = 0;
	CHECK(focal_driver_prepare_capture(&transport) == 0);
	CHECK(fake.count == 4);
	CHECK(fake.operations[0].request == FOCAL_IOCTL_RESET_DEVICE_HL);
	CHECK(fake.operations[0].argument == 0);
	CHECK(fake.operations[1].is_sleep &&
	      fake.operations[1].milliseconds == 10);
	CHECK(fake.operations[2].request == FOCAL_IOCTL_RESET_DEVICE_HL);
	CHECK(fake.operations[2].argument == 1);
	CHECK(fake.operations[3].is_sleep &&
	      fake.operations[3].milliseconds == 10);

	fake.count = 0;
	CHECK(focal_driver_cleanup(&transport) == 0);
	for (size_t index = 0; index < fake.count; index++)
		CHECK(fake.operations[index].request == cleanup[index]);
}

int main(void)
{
	test_stock_config();
	test_stock_sequence();
	puts("FocalTech driver-control tests: PASS");
	return 0;
}
