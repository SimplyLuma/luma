/* SPDX-License-Identifier: Apache-2.0 */

#include "enrollment_protocol.h"
#include "gatekeeper_protocol.h"

#include <stdbool.h>
#include <string.h>

static void secure_clear(void *data, size_t size)
{
	volatile uint8_t *cursor = data;

	while (size--)
		*cursor++ = 0;
}

static int gatekeeper_exchange_checked(
	const struct fp6_enrollment_protocol *protocol, uint32_t command,
	const uint8_t *request, size_t request_size,
	uint8_t *response, size_t response_capacity, size_t *response_size)
{
	int32_t secure_status = 0;
	int result;

	result = protocol->gatekeeper_exchange(
		protocol->gatekeeper_context, command, request, request_size,
		response, response_capacity, response_size, &secure_status);
	if (result)
		return FP6_ENROLLMENT_GATEKEEPER_TRANSPORT;
	return secure_status ? FP6_ENROLLMENT_GATEKEEPER_STATUS :
		FP6_ENROLLMENT_OK;
}

int fp6_enrollment_begin(
	const struct fp6_enrollment_protocol *protocol,
	uint32_t user_id, uint32_t group_id, uint8_t trusted_enrollment,
	const uint8_t *pin, size_t pin_size,
	uint8_t *password_handle, size_t password_handle_capacity,
	struct fp6_enrollment_result_data *result_data)
{
	uint8_t request[FP6_ENROLLMENT_MAX_GATEKEEPER_MESSAGE] = { 0 };
	uint8_t response[FP6_ENROLLMENT_MAX_GATEKEEPER_MESSAGE] = { 0 };
	uint8_t auth_token_wire[GATEKEEPER_AUTH_TOKEN_WIRE_SIZE] = { 0 };
	struct gatekeeper_auth_token auth_token = { 0 };
	struct gatekeeper_buffer empty = { 0 };
	struct gatekeeper_buffer password;
	struct gatekeeper_buffer handle;
	uint64_t challenge = 0;
	uint32_t template_id = group_id;
	size_t request_size = 0;
	size_t response_size = 0;
	bool pre_enroll_started = false;
	int result = FP6_ENROLLMENT_INVALID_ARGUMENT;

	if (!protocol || !protocol->focal.exchange ||
	    !protocol->gatekeeper_exchange || !pin || !pin_size ||
	    !password_handle || !password_handle_capacity || !result_data ||
	    trusted_enrollment > 1U)
		return FP6_ENROLLMENT_INVALID_ARGUMENT;
	memset(result_data, 0, sizeof(*result_data));
	password.data = pin;
	password.size = pin_size;

	result = gatekeeper_encode_enroll(user_id, &empty, &empty, &password,
					  request, sizeof(request),
					  &request_size);
	if (result) {
		result = FP6_ENROLLMENT_GATEKEEPER_PROTOCOL;
		goto out;
	}
	result = gatekeeper_exchange_checked(
		protocol, FP6_GATEKEEPER_ENROLL, request, request_size,
		response, sizeof(response), &response_size);
	secure_clear(request, sizeof(request));
	if (result)
		goto out;
	result = gatekeeper_decode_enroll(response, response_size, &handle);
	if (result) {
		result = FP6_ENROLLMENT_GATEKEEPER_PROTOCOL;
		goto out;
	}
	if (handle.size > password_handle_capacity) {
		result = FP6_ENROLLMENT_CAPACITY;
		goto out;
	}
	memcpy(password_handle, handle.data, handle.size);
	result_data->password_handle_size = handle.size;
	secure_clear(response, sizeof(response));

	result = focal_protocol_pre_enroll(&protocol->focal, &challenge);
	if (result) {
		result = FP6_ENROLLMENT_FOCAL_PROTOCOL;
		goto out;
	}
	pre_enroll_started = true;
	handle.data = password_handle;
	handle.size = result_data->password_handle_size;
	result = gatekeeper_encode_verify(user_id, challenge, &handle,
					  &password, request,
					  sizeof(request), &request_size);
	if (result) {
		result = FP6_ENROLLMENT_GATEKEEPER_PROTOCOL;
		goto out;
	}
	result = gatekeeper_exchange_checked(
		protocol, FP6_GATEKEEPER_VERIFY, request, request_size,
		response, sizeof(response), &response_size);
	secure_clear(request, sizeof(request));
	if (result)
		goto out;
	result = gatekeeper_decode_verify(response, response_size, &auth_token);
	if (result) {
		result = FP6_ENROLLMENT_GATEKEEPER_PROTOCOL;
		goto out;
	}
	/*
	 * Gatekeeper's request user ID is Android's profile ID.  The HAT user ID
	 * is instead Gatekeeper's independently generated secure-user ID from the
	 * password handle; it must not be compared with the profile ID.
	 */
	if (auth_token.challenge != challenge || !auth_token.user_id ||
	    auth_token.authenticator_type != 1U) {
		result = FP6_ENROLLMENT_TOKEN_MISMATCH;
		goto out;
	}
	result = gatekeeper_pack_auth_token(&auth_token, auth_token_wire);
	if (result) {
		result = FP6_ENROLLMENT_GATEKEEPER_PROTOCOL;
		goto out;
	}
	result = focal_protocol_enroll(&protocol->focal, auth_token_wire,
				       &template_id, trusted_enrollment);
	if (result) {
		result = FP6_ENROLLMENT_FOCAL_PROTOCOL;
		goto out;
	}
	result_data->template_id = template_id;
	result = FP6_ENROLLMENT_OK;

out:
	if (result && pre_enroll_started)
		(void)focal_protocol_post_enroll(&protocol->focal);
	secure_clear(&challenge, sizeof(challenge));
	secure_clear(&auth_token, sizeof(auth_token));
	secure_clear(auth_token_wire, sizeof(auth_token_wire));
	secure_clear(response, sizeof(response));
	secure_clear(request, sizeof(request));
	if (result) {
		secure_clear(password_handle, password_handle_capacity);
		memset(result_data, 0, sizeof(*result_data));
	}
	return result;
}
