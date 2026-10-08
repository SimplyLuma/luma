/* SPDX-License-Identifier: Apache-2.0 */

#include "focal_protocol.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct test_transport {
	uint32_t expected_command;
	uint32_t expected_payload_length;
	uint32_t response_command;
	int32_t response_status;
	uint8_t response_payload[FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE];
	uint32_t response_payload_length;
	uint8_t expected_payload[FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE + 8U];
	int calls;
};

static void fail(const char *expression, const char *file, int line)
{
	fprintf(stderr, "FAIL: %s (%s:%d)\n", expression, file, line);
	exit(1);
}

#define CHECK(expression) \
	do { if (!(expression)) fail(#expression, __FILE__, __LINE__); } while (0)

static uint32_t load_u32(const void *source)
{
	uint32_t value;
	memcpy(&value, source, sizeof(value));
	return value;
}

static int test_exchange(void *opaque, const void *request, size_t request_size,
			 void *response, size_t response_capacity,
			 size_t *response_size)
{
	struct test_transport *transport = opaque;
	uint32_t response_header[4];

	CHECK(request_size == 16u + transport->expected_payload_length);
	CHECK(load_u32(request) == transport->expected_command);
	CHECK(load_u32((const uint8_t *)request + 4) ==
	      transport->expected_payload_length);
	CHECK(load_u32((const uint8_t *)request + 8) == 0);
	CHECK(load_u32((const uint8_t *)request + 12) == 0);
	CHECK(!memcmp((const uint8_t *)request + 16,
		      transport->expected_payload,
		      transport->expected_payload_length));
	CHECK(response_capacity >= 16u + transport->response_payload_length);
	response_header[0] = transport->response_command;
	response_header[1] = transport->response_payload_length;
	memcpy(&response_header[2], &transport->response_status,
	       sizeof(transport->response_status));
	response_header[3] = 0;
	memcpy(response, response_header, sizeof(response_header));
	memcpy((uint8_t *)response + sizeof(response_header),
	       transport->response_payload, transport->response_payload_length);
	*response_size = sizeof(response_header) +
		transport->response_payload_length;
	transport->calls++;
	return 0;
}

static void test_probe_device(void)
{
	struct test_transport transport = {
		.expected_command = FOCAL_TA_PROBE_DEVICE,
		.expected_payload_length = 1,
		.response_command = FOCAL_TA_PROBE_DEVICE |
			FOCAL_TA_COMMAND_RESPONSE_BIT,
		.response_payload_length = FOCAL_TA_PROBE_DEVICE_DATA_SIZE,
	};
	const struct focal_protocol protocol = {
		.exchange = test_exchange,
		.context = &transport,
	};
	uint8_t data[FOCAL_TA_PROBE_DEVICE_DATA_SIZE] = {};

	transport.expected_payload[0] = 1;
	for (size_t index = 0; index < transport.response_payload_length; index++)
		transport.response_payload[index] = (uint8_t)(index + 3u);
	CHECK(focal_protocol_probe_device(&protocol, 1, data) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(!memcmp(data, transport.response_payload, sizeof(data)));
}

static void test_enumerate(void)
{
	const struct focal_template_id expected[] = {
		{ .group_id = 7, .finger_id = 41 },
		{ .group_id = 7, .finger_id = 42 },
	};
	struct test_transport transport = {
		.expected_command = FOCAL_TA_ENUMERATE,
		.expected_payload_length = 4,
		.response_command = FOCAL_TA_ENUMERATE |
			FOCAL_TA_COMMAND_RESPONSE_BIT,
		.response_payload_length = 4 + sizeof(expected),
	};
	const struct focal_protocol protocol = {
		.exchange = test_exchange,
		.context = &transport,
	};
	struct focal_template_id templates[4] = {};
	uint32_t count = 2;
	uint32_t requested = 4;
	size_t capacity = 4;

	memcpy(transport.expected_payload, &requested, sizeof(requested));
	memcpy(transport.response_payload, &count, sizeof(count));
	memcpy(transport.response_payload + sizeof(count), expected,
	       sizeof(expected));
	CHECK(focal_protocol_enumerate(&protocol, templates, &capacity) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(capacity == 2);
	CHECK(!memcmp(templates, expected, sizeof(expected)));
	CHECK(transport.calls == 1);
}

static void test_set_active_group(void)
{
	static const char path[] = "/data/vendor_de/0/fpdata";
	struct test_transport transport = {
		.expected_command = FOCAL_TA_SET_ACTIVE_GROUP,
		.expected_payload_length = sizeof(uint32_t) + sizeof(path),
		.response_command = FOCAL_TA_SET_ACTIVE_GROUP |
			FOCAL_TA_COMMAND_RESPONSE_BIT,
	};
	const struct focal_protocol protocol = {
		.exchange = test_exchange,
		.context = &transport,
	};
	uint32_t group = 0;

	memcpy(transport.expected_payload, &group, sizeof(group));
	memcpy(transport.expected_payload + sizeof(group), path, sizeof(path));
	CHECK(focal_protocol_set_active_group(&protocol, group, path) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(transport.calls == 1);

	transport.calls = 0;
	transport.response_status = -2;
	int32_t status = 0;
	CHECK(focal_protocol_set_active_group_status(&protocol, group, path,
						    &status) == FOCAL_PROTOCOL_OK);
	CHECK(status == -2);
	CHECK(transport.calls == 1);
}

static void test_sync_reset_statistics(void)
{
	static const size_t sentinel_offsets[] = {
		0xa8, 0xbc, 0xd0, 0xe4, 0xf8, 0x10c, 0x120, 0x134,
		0x148, 0x15c, 0x170, 0x184, 0x198, 0x1ac, 0x1c0,
		0x1d4, 0x1e8, 0x218,
	};
	struct test_transport transport = {
		.expected_command = FOCAL_TA_SYNC_STATISTICS,
		.expected_payload_length = FOCAL_TA_STATISTICS_SIZE,
		.response_command = FOCAL_TA_SYNC_STATISTICS |
			FOCAL_TA_COMMAND_RESPONSE_BIT,
	};
	const struct focal_protocol protocol = {
		.exchange = test_exchange,
		.context = &transport,
	};
	const int32_t unset = -1;

	for (size_t index = 0; index < sizeof(sentinel_offsets) /
					      sizeof(sentinel_offsets[0]); index++)
		memcpy(transport.expected_payload + sentinel_offsets[index],
		       &unset, sizeof(unset));
	CHECK(focal_protocol_sync_reset_statistics(&protocol) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(transport.calls == 1);
}

static void test_protected_template_sync(void)
{
	struct test_transport transport = {
		.expected_command = FOCAL_TA_SYNC_TEMPLATE,
		.expected_payload_length = 8,
		.response_command = FOCAL_TA_SYNC_TEMPLATE |
			FOCAL_TA_COMMAND_RESPONSE_BIT,
		.response_payload_length = 20,
	};
	const struct focal_protocol protocol = {
		.exchange = test_exchange,
		.context = &transport,
	};
	uint8_t blob[64] = {};
	uint32_t group = 0;
	uint32_t finger = 41;
	uint32_t data_size = 4;
	size_t blob_size = sizeof(blob);

	memcpy(transport.expected_payload, &group, sizeof(group));
	memcpy(transport.expected_payload + 4, &finger, sizeof(finger));
	memcpy(transport.response_payload + 12, &data_size,
	       sizeof(data_size));
	transport.response_payload[16] = 0x81;
	transport.response_payload[17] = 0x22;
	transport.response_payload[18] = 0x43;
	transport.response_payload[19] = 0x64;
	CHECK(focal_protocol_export_protected_template(
		      &protocol, group, finger, blob, &blob_size) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(blob_size == 20);
	CHECK(!memcmp(blob, transport.response_payload, blob_size));

	transport.calls = 0;
	transport.expected_payload_length = 8 + (uint32_t)blob_size;
	memcpy(transport.expected_payload + 8, blob, blob_size);
	transport.response_payload_length = 0;
	CHECK(focal_protocol_import_protected_template(
		      &protocol, group, finger, blob, blob_size) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(transport.calls == 1);

	data_size = 5;
	memcpy(blob + 12, &data_size, sizeof(data_size));
	CHECK(focal_protocol_import_protected_template(
		      &protocol, group, finger, blob, blob_size) ==
	      FOCAL_PROTOCOL_INVALID_ARGUMENT);
}

static void test_initialize(void)
{
	struct test_transport transport = {
		.expected_command = FOCAL_TA_INITIALIZE,
		.expected_payload_length = 0,
		.response_command = FOCAL_TA_INITIALIZE |
			FOCAL_TA_COMMAND_RESPONSE_BIT,
		.response_payload_length = FOCAL_TA_INITIALIZATION_DATA_SIZE,
	};
	const struct focal_protocol protocol = {
		.exchange = test_exchange,
		.context = &transport,
	};
	uint8_t data[FOCAL_TA_INITIALIZATION_DATA_SIZE] = {};

	for (size_t index = 0; index < sizeof(transport.response_payload); index++)
		transport.response_payload[index] = (uint8_t)index;
	transport.response_payload_length = FOCAL_TA_INITIALIZATION_DATA_SIZE;
	CHECK(focal_protocol_initialize(&protocol, data) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(!memcmp(data, transport.response_payload, sizeof(data)));
}

static void test_stock_initialization_primitives(void)
{
	static const char config[] = "{}";
	struct test_transport transport = {
		.expected_command = FOCAL_TA_SYNC_CONFIG,
		.expected_payload_length = sizeof(config),
		.response_command = FOCAL_TA_SYNC_CONFIG |
			FOCAL_TA_COMMAND_RESPONSE_BIT,
	};
	const struct focal_protocol protocol = {
		.exchange = test_exchange,
		.context = &transport,
	};
	uint8_t device_data[FOCAL_TA_DEVICE_DATA_SIZE] = {};
	uint32_t mode = 9;

	memcpy(transport.expected_payload, config, sizeof(config));
	CHECK(focal_protocol_sync_config(&protocol, config, sizeof(config)) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(focal_protocol_sync_config(&protocol, config, sizeof(config) - 1) ==
	      FOCAL_PROTOCOL_INVALID_ARGUMENT);

	transport.expected_command = FOCAL_TA_INITIALIZE_SPI;
	transport.expected_payload_length = 0;
	transport.response_command = FOCAL_TA_INITIALIZE_SPI |
		FOCAL_TA_COMMAND_RESPONSE_BIT;
	CHECK(focal_protocol_initialize_spi(&protocol) == FOCAL_PROTOCOL_OK);

	transport.expected_command = FOCAL_TA_INITIALIZE_DEVICE;
	transport.response_command = FOCAL_TA_INITIALIZE_DEVICE |
		FOCAL_TA_COMMAND_RESPONSE_BIT;
	transport.response_payload_length = FOCAL_TA_DEVICE_DATA_SIZE;
	for (size_t index = 0; index < transport.response_payload_length; index++)
		transport.response_payload[index] = (uint8_t)(index + 1u);
	CHECK(focal_protocol_initialize_device(&protocol, device_data) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(!memcmp(device_data, transport.response_payload,
		      sizeof(device_data)));

	transport.expected_command = FOCAL_TA_CONFIGURE_WORK_MODE;
	transport.expected_payload_length = sizeof(mode);
	transport.response_command = FOCAL_TA_CONFIGURE_WORK_MODE |
		FOCAL_TA_COMMAND_RESPONSE_BIT;
	transport.response_payload_length = 0;
	memcpy(transport.expected_payload, &mode, sizeof(mode));
	CHECK(focal_protocol_configure_work_mode(&protocol, mode) ==
	      FOCAL_PROTOCOL_OK);

	transport.expected_command = FOCAL_TA_FREE_SPI;
	transport.expected_payload_length = 0;
	transport.response_command = FOCAL_TA_FREE_SPI |
		FOCAL_TA_COMMAND_RESPONSE_BIT;
	CHECK(focal_protocol_free_spi(&protocol) == FOCAL_PROTOCOL_OK);
}

static void test_challenge_and_status(void)
{
	struct test_transport transport = {
		.expected_command = FOCAL_TA_PRE_ENROLL,
		.expected_payload_length = 0,
		.response_command = FOCAL_TA_PRE_ENROLL |
			FOCAL_TA_COMMAND_RESPONSE_BIT,
		.response_payload_length = 8,
	};
	const struct focal_protocol protocol = {
		.exchange = test_exchange,
		.context = &transport,
	};
	const uint64_t expected = UINT64_C(0x0123456789abcdef);
	uint64_t challenge = 0;

	memcpy(transport.response_payload, &expected, sizeof(expected));
	CHECK(focal_protocol_pre_enroll(&protocol, &challenge) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(challenge == expected);

	transport.expected_command = FOCAL_TA_CANCEL;
	transport.response_command = FOCAL_TA_CANCEL |
		FOCAL_TA_COMMAND_RESPONSE_BIT;
	transport.response_payload_length = 0;
	transport.response_status = -201;
	CHECK(focal_protocol_cancel(&protocol) ==
	      FOCAL_PROTOCOL_TRUSTLET_ERROR);
}

static void test_authenticated_enroll(void)
{
	struct test_transport transport = {
		.expected_command = FOCAL_TA_ENROLL,
		.expected_payload_length = FOCAL_TA_ENROLL_REQUEST_SIZE,
		.response_command = FOCAL_TA_ENROLL |
			FOCAL_TA_COMMAND_RESPONSE_BIT,
		.response_payload_length = sizeof(uint32_t),
	};
	const struct focal_protocol protocol = {
		.exchange = test_exchange,
		.context = &transport,
	};
	uint8_t token[FOCAL_TA_HARDWARE_AUTH_TOKEN_SIZE];
	uint32_t identifier = 7;
	uint32_t returned = 19;

	for (size_t index = 0; index < sizeof(token); index++)
		token[index] = (uint8_t)(index + 1u);
	memcpy(transport.expected_payload, token, sizeof(token));
	memcpy(transport.expected_payload + sizeof(token), &identifier,
	       sizeof(identifier));
	transport.expected_payload[sizeof(token) + sizeof(identifier)] = 1;
	memcpy(transport.response_payload, &returned, sizeof(returned));
	CHECK(focal_protocol_enroll(&protocol, token, &identifier, 1) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(identifier == returned);
	CHECK(focal_protocol_enroll(&protocol, token, &identifier, 2) ==
	      FOCAL_PROTOCOL_INVALID_ARGUMENT);
}

static void test_event_control(void)
{
	struct test_transport transport = {
		.expected_command = FOCAL_TA_QUERY_EVENT_STATUS,
		.expected_payload_length = sizeof(uint32_t),
		.response_command = FOCAL_TA_QUERY_EVENT_STATUS |
			FOCAL_TA_COMMAND_RESPONSE_BIT,
		.response_status = 5,
	};
	const struct focal_protocol protocol = {
		.exchange = test_exchange,
		.context = &transport,
	};
	uint8_t context[FOCAL_TA_EVENT_CONTEXT_SIZE] = {};
	uint32_t event = 3;
	int32_t status = -1;

	memcpy(transport.expected_payload, &event, sizeof(event));
	CHECK(focal_protocol_query_event_status(&protocol, event, &status) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(status == 5);

	transport.expected_command = FOCAL_TA_REPORT_EVENT;
	transport.expected_payload_length = FOCAL_TA_EVENT_CONTEXT_SIZE;
	transport.response_command = FOCAL_TA_REPORT_EVENT |
		FOCAL_TA_COMMAND_RESPONSE_BIT;
	transport.response_status = 0;
	transport.response_payload_length = FOCAL_TA_EVENT_CONTEXT_SIZE;
	memset(transport.expected_payload, 0,
	       FOCAL_TA_EVENT_CONTEXT_SIZE);
	memcpy(transport.expected_payload + sizeof(uint32_t), &event,
	       sizeof(event));
	for (size_t index = 0; index < FOCAL_TA_EVENT_CONTEXT_SIZE; index++)
		transport.response_payload[index] = (uint8_t)index;
	CHECK(focal_protocol_report_event(&protocol, event, context) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(!memcmp(context, transport.response_payload, sizeof(context)));

	transport.expected_command = FOCAL_TA_CAPTURE_IMAGE;
	transport.expected_payload_length = FOCAL_TA_CAPTURE_CONTEXT_SIZE;
	transport.response_command = FOCAL_TA_CAPTURE_IMAGE |
		FOCAL_TA_COMMAND_RESPONSE_BIT;
	transport.response_payload_length = FOCAL_TA_CAPTURE_CONTEXT_SIZE;
	for (size_t index = 0; index < FOCAL_TA_CAPTURE_CONTEXT_SIZE; index++) {
		transport.expected_payload[index] = (uint8_t)(index + 1u);
		transport.response_payload[index] = (uint8_t)(index + 2u);
	}
	CHECK(focal_protocol_capture_image(&protocol,
		transport.expected_payload, context) == FOCAL_PROTOCOL_OK);
	CHECK(!memcmp(context, transport.response_payload,
		      FOCAL_TA_CAPTURE_CONTEXT_SIZE));

	transport.expected_command = FOCAL_TA_REPORT_EVENT;
	transport.expected_payload_length = FOCAL_TA_EVENT_CONTEXT_SIZE;
	transport.response_command = FOCAL_TA_REPORT_EVENT |
		FOCAL_TA_COMMAND_RESPONSE_BIT;
	transport.response_payload_length = FOCAL_TA_EVENT_CONTEXT_SIZE;
	for (size_t index = 0; index < FOCAL_TA_EVENT_CONTEXT_SIZE; index++) {
		context[index] = (uint8_t)(index + 3u);
		transport.expected_payload[index] = context[index];
		transport.response_payload[index] = (uint8_t)(index + 4u);
	}
	CHECK(focal_protocol_exchange_event(&protocol, context) ==
	      FOCAL_PROTOCOL_OK);
	CHECK(!memcmp(context, transport.response_payload, sizeof(context)));
}

static void test_fail_closed(void)
{
	struct test_transport transport = {
		.expected_command = FOCAL_TA_ENUMERATE,
		.expected_payload_length = 4,
		.response_command = FOCAL_TA_ENUMERATE,
		.response_payload_length = 4,
	};
	const struct focal_protocol protocol = {
		.exchange = test_exchange,
		.context = &transport,
	};
	struct focal_template_id template;
	uint32_t returned = 0;
	uint32_t requested = 1;
	size_t count = 1;

	memcpy(transport.expected_payload, &requested, sizeof(requested));
	memcpy(transport.response_payload, &returned, sizeof(returned));
	CHECK(focal_protocol_enumerate(&protocol, &template, &count) ==
	      FOCAL_PROTOCOL_RESPONSE_COMMAND);
	transport.response_command |= FOCAL_TA_COMMAND_RESPONSE_BIT;
	transport.response_payload_length = 3;
	CHECK(focal_protocol_enumerate(&protocol, &template, &count) ==
	      FOCAL_PROTOCOL_RESPONSE_LENGTH);
}

int main(void)
{
	test_initialize();
	test_probe_device();
	test_stock_initialization_primitives();
	test_enumerate();
	test_set_active_group();
	test_sync_reset_statistics();
	test_protected_template_sync();
	test_challenge_and_status();
	test_authenticated_enroll();
	test_event_control();
	test_fail_closed();
	puts("FocalTech protocol tests: PASS");
	return 0;
}
