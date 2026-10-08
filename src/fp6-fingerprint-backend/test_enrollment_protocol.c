/* SPDX-License-Identifier: Apache-2.0 */

#include "enrollment_protocol.h"

#include <stdio.h>
#include <string.h>

#define CHECK(condition) do { \
	if (!(condition)) { \
		fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, \
			#condition); \
		return 1; \
	} \
} while (0)

struct mock_context {
	unsigned int gatekeeper_calls;
	unsigned int focal_calls;
	uint64_t challenge;
	uint32_t user_id;
	uint32_t secure_user_id;
	uint32_t expected_group;
	uint32_t returned_template;
	uint8_t expected_pin[4];
	uint8_t expected_handle[3];
	uint8_t expected_hmac[32];
	int saw_hardware_token;
};

static int mock_gatekeeper_exchange(
	void *opaque, uint32_t command,
	const void *request, size_t request_size,
	void *response, size_t response_capacity, size_t *response_size,
	int32_t *secure_status)
{
	static const uint8_t enroll_response[] = {
		0xa1, 0x06, 0x43, 0x10, 0x20, 0x30,
	};
	struct mock_context *context = opaque;
	uint8_t *output = response;
	size_t index;

	(void)request;
	(void)request_size;
	if (!response || !response_size || !secure_status)
		return -1;
	*secure_status = 0;
	context->gatekeeper_calls++;
	if (command == FP6_GATEKEEPER_ENROLL) {
		if (response_capacity < sizeof(enroll_response))
			return -1;
		memcpy(output, enroll_response, sizeof(enroll_response));
		*response_size = sizeof(enroll_response);
		return 0;
	}
	if (command != FP6_GATEKEEPER_VERIFY || response_capacity < 92U)
		return -1;
	/* Exact seven-pair CBOR response used by gatekeeper_decode_verify. */
	size_t cursor = 0;
	output[cursor++] = 0xa7;
	output[cursor++] = 0x0b;
	output[cursor++] = 0x00;
	output[cursor++] = 0x04;
	output[cursor++] = 0x1b;
	for (index = 0; index < 8; ++index)
		output[cursor++] = (uint8_t)(context->challenge >>
						(56U - 8U * index));
	output[cursor++] = 0x0c;
	output[cursor++] = 0x1a;
	output[cursor++] = (uint8_t)(context->secure_user_id >> 24);
	output[cursor++] = (uint8_t)(context->secure_user_id >> 16);
	output[cursor++] = (uint8_t)(context->secure_user_id >> 8);
	output[cursor++] = (uint8_t)context->secure_user_id;
	output[cursor++] = 0x0d;
	output[cursor++] = 0x1b;
	for (index = 0; index < 8; ++index)
		output[cursor++] = (uint8_t)(UINT64_C(0x3132333435363738) >>
						(56U - 8U * index));
	output[cursor++] = 0x0e;
	output[cursor++] = 0x01;
	output[cursor++] = 0x0f;
	output[cursor++] = 0x1b;
	for (index = 0; index < 8; ++index)
		output[cursor++] = (uint8_t)(UINT64_C(0x4142434445464748) >>
						(56U - 8U * index));
	output[cursor++] = 0x10;
	output[cursor++] = 0x58;
	output[cursor++] = 0x20;
	memcpy(output + cursor, context->expected_hmac,
	       sizeof(context->expected_hmac));
	cursor += sizeof(context->expected_hmac);
	*response_size = cursor;
	return 0;
}

struct focal_header {
	uint32_t command;
	uint32_t payload_length;
	int32_t status;
	uint32_t flags;
};

static int mock_focal_exchange(void *opaque,
			       const void *request, size_t request_size,
			       void *response, size_t response_capacity,
			       size_t *response_size)
{
	struct mock_context *context = opaque;
	struct focal_header input;
	struct focal_header output = { 0 };
	const uint8_t *payload;
	uint8_t *reply = response;
	uint32_t template_id;

	if (!request || request_size < sizeof(input) || !response ||
	    response_capacity < sizeof(output) || !response_size)
		return -1;
	memcpy(&input, request, sizeof(input));
	if (request_size != sizeof(input) + input.payload_length)
		return -1;
	payload = (const uint8_t *)request + sizeof(input);
	output.command = input.command | FOCAL_TA_COMMAND_RESPONSE_BIT;
	context->focal_calls++;
	if (input.command == FOCAL_TA_PRE_ENROLL) {
		output.payload_length = sizeof(context->challenge);
		memcpy(reply, &output, sizeof(output));
		memcpy(reply + sizeof(output), &context->challenge,
		       sizeof(context->challenge));
		*response_size = sizeof(output) + sizeof(context->challenge);
		return 0;
	}
	if (input.command == FOCAL_TA_POST_ENROLL) {
		memcpy(reply, &output, sizeof(output));
		*response_size = sizeof(output);
		return 0;
	}
	if (input.command != FOCAL_TA_ENROLL ||
	    input.payload_length != FOCAL_TA_ENROLL_REQUEST_SIZE)
		return -1;
	/* Token scalars must be packed in Android's network-order HAT ABI. */
	if (!memcmp(payload + 1,
		    "\x01\x02\x03\x04\x05\x06\x07\x08", 8) &&
	    !memcmp(payload + 9, "\x00\x00\x00\x00", 4) &&
	    !memcmp(payload + 37, context->expected_hmac,
		    sizeof(context->expected_hmac)))
		context->saw_hardware_token = 1;
	memcpy(&template_id, payload + FOCAL_TA_HARDWARE_AUTH_TOKEN_SIZE,
	       sizeof(template_id));
	if (template_id != context->expected_group ||
	    payload[FOCAL_TA_ENROLL_REQUEST_SIZE - 1] != 1U)
		return -1;
	output.payload_length = sizeof(context->returned_template);
	memcpy(reply, &output, sizeof(output));
	memcpy(reply + sizeof(output), &context->returned_template,
	       sizeof(context->returned_template));
	*response_size = sizeof(output) + sizeof(context->returned_template);
	return 0;
}

int main(void)
{
	struct mock_context context = {
		.challenge = UINT64_C(0x0102030405060708),
		.user_id = 0x1234,
		.secure_user_id = 0x55667788,
		.expected_group = 7,
		.returned_template = 42,
		.expected_pin = { '1', '2', '3', '4' },
		.expected_handle = { 0x10, 0x20, 0x30 },
	};
	struct fp6_enrollment_protocol protocol = {
		.focal = { mock_focal_exchange, &context },
		.gatekeeper_exchange = mock_gatekeeper_exchange,
		.gatekeeper_context = &context,
	};
	struct fp6_enrollment_result_data result_data;
	uint8_t handle[64] = { 0 };
	size_t index;

	for (index = 0; index < sizeof(context.expected_hmac); ++index)
		context.expected_hmac[index] = (uint8_t)index;
	CHECK(fp6_enrollment_begin(
		&protocol, context.user_id, context.expected_group, 1,
		context.expected_pin, sizeof(context.expected_pin), handle,
		sizeof(handle), &result_data) == FP6_ENROLLMENT_OK);
	CHECK(context.gatekeeper_calls == 2);
	CHECK(context.focal_calls == 2);
	CHECK(context.saw_hardware_token == 1);
	CHECK(result_data.template_id == context.returned_template);
	CHECK(result_data.password_handle_size ==
	      sizeof(context.expected_handle));
	CHECK(!memcmp(handle, context.expected_handle,
		      sizeof(context.expected_handle)));
	puts("FP6 enrollment orchestration tests: PASS");
	return 0;
}
