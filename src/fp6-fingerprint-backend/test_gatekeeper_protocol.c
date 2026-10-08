/* SPDX-License-Identifier: Apache-2.0 */

#include "gatekeeper_protocol.h"

#include <stdio.h>
#include <string.h>

#define CHECK(condition) do { \
	if (!(condition)) { \
		fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, \
			#condition); \
		return 1; \
	} \
} while (0)

static int test_enroll_encoding(void)
{
	static const uint8_t expected_new[] = {
		0xa2, 0x03, 0x19, 0x12, 0x34, 0x08, 0x44,
		0x31, 0x32, 0x33, 0x34,
	};
	static const uint8_t expected_change[] = {
		0xa4, 0x03, 0x01, 0x05, 0x42, 0x01, 0x02,
		0x07, 0x43, 0x6f, 0x6c, 0x64,
		0x08, 0x43, 0x6e, 0x65, 0x77,
	};
	const uint8_t pin[] = { '1', '2', '3', '4' };
	const uint8_t handle_data[] = { 1, 2 };
	const uint8_t old_data[] = { 'o', 'l', 'd' };
	const uint8_t new_data[] = { 'n', 'e', 'w' };
	struct gatekeeper_buffer empty = {};
	struct gatekeeper_buffer handle = { handle_data, sizeof(handle_data) };
	struct gatekeeper_buffer old = { old_data, sizeof(old_data) };
	struct gatekeeper_buffer desired = { pin, sizeof(pin) };
	uint8_t encoded[64];
	size_t encoded_size;

	CHECK(gatekeeper_encode_enroll(0x1234, &empty, &empty, &desired,
				       encoded, sizeof(encoded), &encoded_size) == 0);
	CHECK(encoded_size == sizeof(expected_new));
	CHECK(!memcmp(encoded, expected_new, sizeof(expected_new)));
	desired.data = new_data;
	desired.size = sizeof(new_data);
	CHECK(gatekeeper_encode_enroll(1, &handle, &old, &desired, encoded,
				       sizeof(encoded), &encoded_size) == 0);
	CHECK(encoded_size == sizeof(expected_change));
	CHECK(!memcmp(encoded, expected_change, sizeof(expected_change)));
	CHECK(gatekeeper_encode_enroll(1, &handle, &empty, &desired, encoded,
				       sizeof(encoded), &encoded_size) ==
	      GATEKEEPER_PROTOCOL_INVALID_ARGUMENT);
	return 0;
}

static int test_verify_encoding(void)
{
	static const uint8_t expected[] = {
		0xa4, 0x03, 0x01, 0x04, 0x1b, 0x01, 0x02, 0x03, 0x04,
		0x05, 0x06, 0x07, 0x08, 0x06, 0x42, 0xaa, 0xbb, 0x09,
		0x44, 0x31, 0x32, 0x33, 0x34,
	};
	const uint8_t handle_data[] = { 0xaa, 0xbb };
	const uint8_t pin[] = { '1', '2', '3', '4' };
	struct gatekeeper_buffer handle = { handle_data, sizeof(handle_data) };
	struct gatekeeper_buffer password = { pin, sizeof(pin) };
	uint8_t encoded[64];
	size_t encoded_size;

	CHECK(gatekeeper_encode_verify(1, UINT64_C(0x0102030405060708),
				       &handle, &password, encoded,
				       sizeof(encoded), &encoded_size) == 0);
	CHECK(encoded_size == sizeof(expected));
	CHECK(!memcmp(encoded, expected, sizeof(expected)));
	return 0;
}

static int test_response_decoding(void)
{
	static const uint8_t enroll[] = { 0xa1, 0x06, 0x43, 0x10, 0x20, 0x30 };
	static const uint8_t verify[] = {
		0xa7, 0x0b, 0x00,
		0x04, 0x1b, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
		0x0c, 0x1b, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18,
		0x0d, 0x1b, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
		0x0e, 0x01,
		0x0f, 0x1b, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38,
		0x10, 0x58, 0x20,
		0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
		0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f,
		0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,
		0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f,
	};
	struct gatekeeper_buffer handle;
	struct gatekeeper_auth_token token;
	size_t index;

	CHECK(gatekeeper_decode_enroll(enroll, sizeof(enroll), &handle) == 0);
	CHECK(handle.size == 3 && handle.data[0] == 0x10 &&
	      handle.data[2] == 0x30);
	CHECK(gatekeeper_decode_verify(verify, sizeof(verify), &token) == 0);
	CHECK(token.version == 0);
	CHECK(token.challenge == UINT64_C(0x0102030405060708));
	CHECK(token.user_id == UINT64_C(0x1112131415161718));
	CHECK(token.authenticator_id == UINT64_C(0x2122232425262728));
	CHECK(token.authenticator_type == 1);
	CHECK(token.timestamp == UINT64_C(0x3132333435363738));
	for (index = 0; index < sizeof(token.hmac); ++index)
		CHECK(token.hmac[index] == index);
	CHECK(gatekeeper_decode_verify(verify, sizeof(verify) - 1, &token) ==
	      GATEKEEPER_PROTOCOL_MALFORMED);
	return 0;
}

static int test_auth_token_wire_encoding(void)
{
	struct gatekeeper_auth_token token = {
		.version = 1,
		.challenge = UINT64_C(0x0102030405060708),
		.user_id = UINT64_C(0x1112131415161718),
		.authenticator_id = UINT64_C(0x2122232425262728),
		.authenticator_type = UINT32_C(0x31323334),
		.timestamp = UINT64_C(0x4142434445464748),
	};
	uint8_t wire[GATEKEEPER_AUTH_TOKEN_WIRE_SIZE];
	size_t index;

	for (index = 0; index < sizeof(token.hmac); ++index)
		token.hmac[index] = (uint8_t)(0xa0U + index);
	CHECK(gatekeeper_pack_auth_token(&token, wire) == 0);
	CHECK(wire[0] == 1);
	CHECK(!memcmp(wire + 1,
		       "\x01\x02\x03\x04\x05\x06\x07\x08", 8));
	CHECK(!memcmp(wire + 9,
		       "\x11\x12\x13\x14\x15\x16\x17\x18", 8));
	CHECK(!memcmp(wire + 17,
		       "\x21\x22\x23\x24\x25\x26\x27\x28", 8));
	CHECK(!memcmp(wire + 25, "\x31\x32\x33\x34", 4));
	CHECK(!memcmp(wire + 29,
		       "\x41\x42\x43\x44\x45\x46\x47\x48", 8));
	CHECK(!memcmp(wire + 37, token.hmac, sizeof(token.hmac)));
	CHECK(gatekeeper_pack_auth_token(NULL, wire) ==
	      GATEKEEPER_PROTOCOL_INVALID_ARGUMENT);
	return 0;
}

int main(void)
{
	CHECK(test_enroll_encoding() == 0);
	CHECK(test_verify_encoding() == 0);
	CHECK(test_response_decoding() == 0);
	CHECK(test_auth_token_wire_encoding() == 0);
	puts("gatekeeper protocol tests: PASS");
	return 0;
}
