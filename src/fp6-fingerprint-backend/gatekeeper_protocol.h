/* SPDX-License-Identifier: Apache-2.0 */

#ifndef LUMA_FP6_GATEKEEPER_PROTOCOL_H
#define LUMA_FP6_GATEKEEPER_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define GATEKEEPER_AUTH_TOKEN_HMAC_SIZE 32U
#define GATEKEEPER_AUTH_TOKEN_WIRE_SIZE 69U

enum gatekeeper_protocol_result {
	GATEKEEPER_PROTOCOL_OK = 0,
	GATEKEEPER_PROTOCOL_INVALID_ARGUMENT = -1,
	GATEKEEPER_PROTOCOL_CAPACITY = -2,
	GATEKEEPER_PROTOCOL_MALFORMED = -3,
	GATEKEEPER_PROTOCOL_UNEXPECTED_FIELD = -4,
};

struct gatekeeper_buffer {
	const uint8_t *data;
	size_t size;
};

struct gatekeeper_auth_token {
	uint8_t version;
	uint64_t challenge;
	uint64_t user_id;
	uint64_t authenticator_id;
	uint32_t authenticator_type;
	uint64_t timestamp;
	uint8_t hmac[GATEKEEPER_AUTH_TOKEN_HMAC_SIZE];
};

int gatekeeper_encode_enroll(uint32_t user_id,
			     const struct gatekeeper_buffer *current_handle,
			     const struct gatekeeper_buffer *current_password,
			     const struct gatekeeper_buffer *desired_password,
			     uint8_t *output, size_t output_capacity,
			     size_t *output_size);

int gatekeeper_decode_enroll(const uint8_t *input, size_t input_size,
			     struct gatekeeper_buffer *password_handle);

int gatekeeper_encode_verify(uint32_t user_id, uint64_t challenge,
			     const struct gatekeeper_buffer *enrolled_handle,
			     const struct gatekeeper_buffer *provided_password,
			     uint8_t *output, size_t output_capacity,
			     size_t *output_size);

int gatekeeper_decode_verify(const uint8_t *input, size_t input_size,
			     struct gatekeeper_auth_token *token);

int gatekeeper_pack_auth_token(
	const struct gatekeeper_auth_token *token,
	uint8_t output[GATEKEEPER_AUTH_TOKEN_WIRE_SIZE]);

#endif
