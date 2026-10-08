/* SPDX-License-Identifier: Apache-2.0 */

#include "gatekeeper_protocol.h"

#include <stdbool.h>
#include <string.h>

struct cbor_writer {
	uint8_t *data;
	size_t capacity;
	size_t offset;
};

struct cbor_reader {
	const uint8_t *data;
	size_t size;
	size_t offset;
};

static int writer_append(struct cbor_writer *writer, const void *data,
			 size_t size)
{
	if (size > writer->capacity - writer->offset)
		return GATEKEEPER_PROTOCOL_CAPACITY;
	if (size)
		memcpy(writer->data + writer->offset, data, size);
	writer->offset += size;
	return GATEKEEPER_PROTOCOL_OK;
}

static int writer_byte(struct cbor_writer *writer, uint8_t value)
{
	return writer_append(writer, &value, sizeof(value));
}

static int encode_argument(struct cbor_writer *writer, uint8_t major,
			   uint64_t value)
{
	uint8_t encoded[9];
	size_t size;

	if (value < 24U) {
		encoded[0] = (uint8_t)((major << 5) | (uint8_t)value);
		size = 1;
	} else if (value <= UINT8_MAX) {
		encoded[0] = (uint8_t)(((unsigned int)major << 5) | 24U);
		encoded[1] = (uint8_t)value;
		size = 2;
	} else if (value <= UINT16_MAX) {
		encoded[0] = (uint8_t)(((unsigned int)major << 5) | 25U);
		encoded[1] = (uint8_t)(value >> 8);
		encoded[2] = (uint8_t)value;
		size = 3;
	} else if (value <= UINT32_MAX) {
		encoded[0] = (uint8_t)(((unsigned int)major << 5) | 26U);
		encoded[1] = (uint8_t)(value >> 24);
		encoded[2] = (uint8_t)(value >> 16);
		encoded[3] = (uint8_t)(value >> 8);
		encoded[4] = (uint8_t)value;
		size = 5;
	} else {
		encoded[0] = (uint8_t)(((unsigned int)major << 5) | 27U);
		encoded[1] = (uint8_t)(value >> 56);
		encoded[2] = (uint8_t)(value >> 48);
		encoded[3] = (uint8_t)(value >> 40);
		encoded[4] = (uint8_t)(value >> 32);
		encoded[5] = (uint8_t)(value >> 24);
		encoded[6] = (uint8_t)(value >> 16);
		encoded[7] = (uint8_t)(value >> 8);
		encoded[8] = (uint8_t)value;
		size = 9;
	}
	return writer_append(writer, encoded, size);
}

static int encode_uint(struct cbor_writer *writer, uint64_t value)
{
	return encode_argument(writer, 0, value);
}

static int encode_bytes(struct cbor_writer *writer,
			const struct gatekeeper_buffer *buffer)
{
	int result;

	result = encode_argument(writer, 2, buffer->size);
	if (result)
		return result;
	return writer_append(writer, buffer->data, buffer->size);
}

static int encode_pair_uint(struct cbor_writer *writer, uint64_t label,
			    uint64_t value)
{
	int result = encode_uint(writer, label);

	return result ? result : encode_uint(writer, value);
}

static int encode_pair_bytes(struct cbor_writer *writer, uint64_t label,
			     const struct gatekeeper_buffer *buffer)
{
	int result = encode_uint(writer, label);

	return result ? result : encode_bytes(writer, buffer);
}

static bool valid_buffer(const struct gatekeeper_buffer *buffer,
			 bool allow_empty)
{
	return buffer && (allow_empty || buffer->size) &&
		(!buffer->size || buffer->data);
}

int gatekeeper_encode_enroll(uint32_t user_id,
			     const struct gatekeeper_buffer *current_handle,
			     const struct gatekeeper_buffer *current_password,
			     const struct gatekeeper_buffer *desired_password,
			     uint8_t *output, size_t output_capacity,
			     size_t *output_size)
{
	struct cbor_writer writer = { output, output_capacity, 0 };
	bool reenroll;
	int result;

	if (!output || !output_size || !valid_buffer(desired_password, false) ||
	    !valid_buffer(current_handle, true) ||
	    !valid_buffer(current_password, true) ||
	    (!!current_handle->size != !!current_password->size))
		return GATEKEEPER_PROTOCOL_INVALID_ARGUMENT;
	*output_size = 0;
	reenroll = current_handle->size != 0;
	result = writer_byte(&writer, reenroll ? 0xa4U : 0xa2U);
	if (!result)
		result = encode_pair_uint(&writer, 3, user_id);
	if (!result && reenroll)
		result = encode_pair_bytes(&writer, 5, current_handle);
	if (!result && reenroll)
		result = encode_pair_bytes(&writer, 7, current_password);
	if (!result)
		result = encode_pair_bytes(&writer, 8, desired_password);
	if (result)
		return result;
	*output_size = writer.offset;
	return GATEKEEPER_PROTOCOL_OK;
}

static int reader_byte(struct cbor_reader *reader, uint8_t *value)
{
	if (reader->offset == reader->size)
		return GATEKEEPER_PROTOCOL_MALFORMED;
	*value = reader->data[reader->offset++];
	return GATEKEEPER_PROTOCOL_OK;
}

static int decode_argument(struct cbor_reader *reader, uint8_t expected_major,
			   uint64_t *value)
{
	uint8_t first;
	uint8_t additional;
	uint8_t count;
	uint64_t decoded = 0;
	int result;

	result = reader_byte(reader, &first);
	if (result || (first >> 5) != expected_major)
		return GATEKEEPER_PROTOCOL_MALFORMED;
	additional = first & 0x1fU;
	if (additional < 24U) {
		*value = additional;
		return GATEKEEPER_PROTOCOL_OK;
	}
	if (additional == 24U)
		count = 1;
	else if (additional == 25U)
		count = 2;
	else if (additional == 26U)
		count = 4;
	else if (additional == 27U)
		count = 8;
	else
		return GATEKEEPER_PROTOCOL_MALFORMED;
	if ((size_t)count > reader->size - reader->offset)
		return GATEKEEPER_PROTOCOL_MALFORMED;
	while (count--)
		decoded = (decoded << 8) | reader->data[reader->offset++];
	*value = decoded;
	return GATEKEEPER_PROTOCOL_OK;
}

static int expect_map(struct cbor_reader *reader, uint64_t pairs)
{
	uint64_t actual;
	int result = decode_argument(reader, 5, &actual);

	return result || actual != pairs ? GATEKEEPER_PROTOCOL_MALFORMED :
		GATEKEEPER_PROTOCOL_OK;
}

static int expect_label(struct cbor_reader *reader, uint64_t expected)
{
	uint64_t actual;
	int result = decode_argument(reader, 0, &actual);

	if (result)
		return result;
	return actual == expected ? GATEKEEPER_PROTOCOL_OK :
		GATEKEEPER_PROTOCOL_UNEXPECTED_FIELD;
}

static int decode_uint(struct cbor_reader *reader, uint64_t label,
			uint64_t *value)
{
	int result = expect_label(reader, label);

	return result ? result : decode_argument(reader, 0, value);
}

static int decode_bytes(struct cbor_reader *reader, uint64_t label,
			struct gatekeeper_buffer *buffer)
{
	uint64_t size;
	int result = expect_label(reader, label);

	if (result)
		return result;
	result = decode_argument(reader, 2, &size);
	if (result || size > SIZE_MAX || (size_t)size > reader->size - reader->offset)
		return GATEKEEPER_PROTOCOL_MALFORMED;
	buffer->data = reader->data + reader->offset;
	buffer->size = (size_t)size;
	reader->offset += (size_t)size;
	return GATEKEEPER_PROTOCOL_OK;
}

int gatekeeper_decode_enroll(const uint8_t *input, size_t input_size,
			     struct gatekeeper_buffer *password_handle)
{
	struct cbor_reader reader = { input, input_size, 0 };
	int result;

	if (!input || !password_handle)
		return GATEKEEPER_PROTOCOL_INVALID_ARGUMENT;
	password_handle->data = NULL;
	password_handle->size = 0;
	result = expect_map(&reader, 1);
	if (!result)
		result = decode_bytes(&reader, 6, password_handle);
	if (!result && (!password_handle->size || reader.offset != reader.size))
		result = GATEKEEPER_PROTOCOL_MALFORMED;
	return result;
}

int gatekeeper_encode_verify(uint32_t user_id, uint64_t challenge,
			     const struct gatekeeper_buffer *enrolled_handle,
			     const struct gatekeeper_buffer *provided_password,
			     uint8_t *output, size_t output_capacity,
			     size_t *output_size)
{
	struct cbor_writer writer = { output, output_capacity, 0 };
	int result;

	if (!output || !output_size || !valid_buffer(enrolled_handle, false) ||
	    !valid_buffer(provided_password, false))
		return GATEKEEPER_PROTOCOL_INVALID_ARGUMENT;
	*output_size = 0;
	result = writer_byte(&writer, 0xa4U);
	if (!result)
		result = encode_pair_uint(&writer, 3, user_id);
	if (!result)
		result = encode_pair_uint(&writer, 4, challenge);
	if (!result)
		result = encode_pair_bytes(&writer, 6, enrolled_handle);
	if (!result)
		result = encode_pair_bytes(&writer, 9, provided_password);
	if (result)
		return result;
	*output_size = writer.offset;
	return GATEKEEPER_PROTOCOL_OK;
}

int gatekeeper_decode_verify(const uint8_t *input, size_t input_size,
			     struct gatekeeper_auth_token *token)
{
	struct cbor_reader reader = { input, input_size, 0 };
	struct gatekeeper_buffer hmac = {};
	uint64_t version;
	uint64_t authenticator_type;
	int result;

	if (!input || !token)
		return GATEKEEPER_PROTOCOL_INVALID_ARGUMENT;
	memset(token, 0, sizeof(*token));
	result = expect_map(&reader, 7);
	if (!result)
		result = decode_uint(&reader, 11, &version);
	if (!result && version > UINT8_MAX)
		result = GATEKEEPER_PROTOCOL_MALFORMED;
	if (!result)
		result = decode_uint(&reader, 4, &token->challenge);
	if (!result)
		result = decode_uint(&reader, 12, &token->user_id);
	if (!result)
		result = decode_uint(&reader, 13, &token->authenticator_id);
	if (!result)
		result = decode_uint(&reader, 14, &authenticator_type);
	if (!result && authenticator_type > UINT32_MAX)
		result = GATEKEEPER_PROTOCOL_MALFORMED;
	if (!result)
		result = decode_uint(&reader, 15, &token->timestamp);
	if (!result)
		result = decode_bytes(&reader, 16, &hmac);
	if (!result && (hmac.size != sizeof(token->hmac) ||
			reader.offset != reader.size))
		result = GATEKEEPER_PROTOCOL_MALFORMED;
	if (result) {
		memset(token, 0, sizeof(*token));
		return result;
	}
	token->version = (uint8_t)version;
	token->authenticator_type = (uint32_t)authenticator_type;
	memcpy(token->hmac, hmac.data, sizeof(token->hmac));
	return GATEKEEPER_PROTOCOL_OK;
}

static void store_be32(uint8_t *output, uint32_t value)
{
	output[0] = (uint8_t)(value >> 24);
	output[1] = (uint8_t)(value >> 16);
	output[2] = (uint8_t)(value >> 8);
	output[3] = (uint8_t)value;
}

static void store_be64(uint8_t *output, uint64_t value)
{
	output[0] = (uint8_t)(value >> 56);
	output[1] = (uint8_t)(value >> 48);
	output[2] = (uint8_t)(value >> 40);
	output[3] = (uint8_t)(value >> 32);
	output[4] = (uint8_t)(value >> 24);
	output[5] = (uint8_t)(value >> 16);
	output[6] = (uint8_t)(value >> 8);
	output[7] = (uint8_t)value;
}

int gatekeeper_pack_auth_token(
	const struct gatekeeper_auth_token *token,
	uint8_t output[GATEKEEPER_AUTH_TOKEN_WIRE_SIZE])
{
	if (!token || !output)
		return GATEKEEPER_PROTOCOL_INVALID_ARGUMENT;
	output[0] = token->version;
	store_be64(output + 1, token->challenge);
	store_be64(output + 9, token->user_id);
	store_be64(output + 17, token->authenticator_id);
	store_be32(output + 25, token->authenticator_type);
	store_be64(output + 29, token->timestamp);
	memcpy(output + 37, token->hmac, sizeof(token->hmac));
	return GATEKEEPER_PROTOCOL_OK;
}
