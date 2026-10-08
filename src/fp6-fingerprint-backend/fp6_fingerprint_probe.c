/* SPDX-License-Identifier: Apache-2.0 */

/*
 * Bounded lab probe for the FP6 signed FocalTech trustlet. This binary can
 * attach only to the fixed focal64 application and exposes only non-capture
 * identity operations. It does not load a TA or touch /dev/focaltech_fp.
 */

#include "focal_protocol.h"
#include "../fp6-fingerprint-qsee-bridge/QSEEComAPI.h"

#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define FOCAL_QSEECOM_BUFFER_SIZE UINT32_C(0x80040)

struct qsee_transport {
	struct QSEECom_handle *handle;
};

static uint32_t load_u32(const void *source)
{
	uint32_t value;

	memcpy(&value, source, sizeof(value));
	return value;
}

static int qsee_exchange(void *opaque, const void *request, size_t request_size,
			 void *response, size_t response_capacity,
			 size_t *response_size)
{
	struct qsee_transport *transport = opaque;
	uint32_t payload_size;

	if (request_size > UINT32_MAX || response_capacity > UINT32_MAX)
		return -1;
	if (QSEECom_send_cmd(transport->handle, (void *)request,
			     (uint32_t)request_size, response,
			     (uint32_t)response_capacity))
		return -1;
	if (response_capacity < 16)
		return -1;
	payload_size = load_u32((const uint8_t *)response + 4);
	if (payload_size > response_capacity - 16)
		return -1;
	*response_size = 16u + payload_size;
	return 0;
}

static int run_enumerate(const struct focal_protocol *protocol)
{
	struct focal_template_id templates[FOCAL_TA_MAX_TEMPLATES] = {};
	size_t count = FOCAL_TA_MAX_TEMPLATES;
	int result;

	result = focal_protocol_enumerate(protocol, templates, &count);
	if (result) {
		fprintf(stderr, "event=fingerprint_enumerate result=%d\n", result);
		return 1;
	}
	printf("event=fingerprint_enumerate result=accepted count=%zu\n", count);
	for (size_t index = 0; index < count; index++)
		printf("event=fingerprint_template index=%zu group=%" PRIu32
		       " finger=%" PRIu32 "\n", index,
		       templates[index].group_id, templates[index].finger_id);
	return 0;
}

static int run_set_active_group(const struct focal_protocol *protocol)
{
	int result = focal_protocol_set_active_group(
		protocol, 0, "/data/vendor_de/0/fpdata");

	if (result) {
		fprintf(stderr,
			"event=fingerprint_set_active_group result=%d\n", result);
		return 1;
	}
	puts("event=fingerprint_set_active_group result=accepted group=0");
	return 0;
}

static int run_probe_device(const struct focal_protocol *protocol)
{
	uint8_t data[FOCAL_TA_PROBE_DEVICE_DATA_SIZE] = {};
	int result;

	result = focal_protocol_probe_device(protocol, 0, data);
	if (result) {
		fprintf(stderr, "event=fingerprint_probe_device result=%d\n",
			result);
		return 1;
	}
	puts("event=fingerprint_probe_device result=accepted bytes=60");
	return 0;
}

static int run_u64(const struct focal_protocol *protocol, const char *operation)
{
	uint64_t value = 0;
	int result;

	if (!strcmp(operation, "pre-enroll"))
		result = focal_protocol_pre_enroll(protocol, &value);
	else
		result = focal_protocol_get_authenticator_id(protocol, &value);
	if (result) {
		fprintf(stderr, "event=fingerprint_%s result=%d\n", operation,
			result);
		return 1;
	}
	printf("event=fingerprint_%s result=accepted value=%" PRIu64 "\n",
	       operation, value);
	return 0;
}

static int run_empty(const struct focal_protocol *protocol,
		     const char *operation)
{
	int result;

	if (!strcmp(operation, "post-enroll"))
		result = focal_protocol_post_enroll(protocol);
	else
		result = focal_protocol_cancel(protocol);
	if (result) {
		fprintf(stderr, "event=fingerprint_%s result=%d\n", operation,
			result);
		return 1;
	}
	printf("event=fingerprint_%s result=accepted\n", operation);
	return 0;
}

int main(int argc, char **argv)
{
	struct qsee_transport transport = {};
	uint8_t initialization_data[FOCAL_TA_INITIALIZATION_DATA_SIZE] = {};
	const struct focal_protocol protocol = {
		.exchange = qsee_exchange,
		.context = &transport,
	};
	const char *operation;
	int result = 1;

	if (argc != 2) {
		fprintf(stderr, "usage: %s probe-device|enumerate|set-active-group|pre-enroll|post-enroll|auth-id|cancel\n",
			argv[0]);
		return 2;
	}
	if (geteuid() != 0) {
		fputs("error: probe requires the root-only TEE boundary\n", stderr);
		return 2;
	}
	operation = argv[1];
	if (strcmp(operation, "probe-device") &&
	    strcmp(operation, "enumerate") &&
	    strcmp(operation, "set-active-group") &&
	    strcmp(operation, "pre-enroll") &&
	    strcmp(operation, "post-enroll") &&
	    strcmp(operation, "auth-id") && strcmp(operation, "cancel")) {
		fprintf(stderr, "error: unsupported operation: %s\n", operation);
		return 2;
	}

	if (QSEECom_start_app(&transport.handle, NULL, "focal64",
			      FOCAL_QSEECOM_BUFFER_SIZE)) {
		fprintf(stderr, "event=fingerprint_attach result=failed errno=%d\n",
			errno);
		return 1;
	}
	puts("event=fingerprint_attach result=accepted app=focal64");
	result = focal_protocol_initialize(&protocol, initialization_data);
	if (result) {
		fprintf(stderr, "event=fingerprint_initialize result=%d\n", result);
		goto detach;
	}
	puts("event=fingerprint_initialize result=accepted bytes=192");

	if (!strcmp(operation, "probe-device"))
		result = run_probe_device(&protocol);
	else if (!strcmp(operation, "enumerate"))
		result = run_enumerate(&protocol);
	else if (!strcmp(operation, "set-active-group"))
		result = run_set_active_group(&protocol);
	else if (!strcmp(operation, "pre-enroll") ||
		 !strcmp(operation, "auth-id"))
		result = run_u64(&protocol, operation);
	else
		result = run_empty(&protocol, operation);

detach:
	if (QSEECom_shutdown_app(&transport.handle)) {
		fprintf(stderr, "event=fingerprint_detach result=failed errno=%d\n",
			errno);
		return 1;
	}
	puts("event=fingerprint_detach result=accepted");
	return result;
}
