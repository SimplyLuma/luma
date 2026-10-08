/* SPDX-License-Identifier: Apache-2.0 */

#include "focal_protocol.h"

#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

struct focal_message_header {
	uint32_t command;
	uint32_t payload_length;
	int32_t status;
	uint32_t elapsed_or_flags;
};

_Static_assert(sizeof(struct focal_message_header) == 16,
	       "FocalTech response header ABI changed");
_Static_assert(sizeof(struct focal_template_id) == 8,
	       "FocalTech template identifier ABI changed");

static int exchange_empty(const struct focal_protocol *protocol,
			  uint32_t command);

static int exchange_command_status(const struct focal_protocol *protocol,
				   uint32_t command, const void *payload,
				   size_t payload_size, void *response_payload,
				   size_t response_capacity,
				   size_t *response_payload_size,
				   int32_t *trustlet_status);

static uint32_t load_u32(const void *source)
{
	uint32_t value;

	memcpy(&value, source, sizeof(value));
	return value;
}

static uint64_t load_u64(const void *source)
{
	uint64_t value;

	memcpy(&value, source, sizeof(value));
	return value;
}

static int exchange_command(const struct focal_protocol *protocol,
			    uint32_t command, const void *payload,
			    size_t payload_size, void *response_payload,
			    size_t response_capacity,
			    size_t *response_payload_size)
{
	return exchange_command_status(protocol, command, payload, payload_size,
				       response_payload, response_capacity,
				       response_payload_size, NULL);
}

static int exchange_command_status(const struct focal_protocol *protocol,
				   uint32_t command, const void *payload,
				   size_t payload_size, void *response_payload,
				   size_t response_capacity,
				   size_t *response_payload_size,
				   int32_t *trustlet_status)
{
	uint8_t *request = NULL;
	uint8_t *response = NULL;
	struct focal_message_header request_header = {};
	struct focal_message_header response_header;
	size_t request_size;
	size_t response_size = 0;
	int result = FOCAL_PROTOCOL_INVALID_ARGUMENT;

	if (!protocol || !protocol->exchange ||
	    payload_size > FOCAL_TA_REQUEST_BUFFER_SIZE - sizeof(request_header) ||
	    response_capacity > FOCAL_TA_RESPONSE_BUFFER_SIZE -
				      sizeof(response_header) ||
	    (payload_size && !payload) ||
	    (response_capacity && !response_payload) ||
	    !response_payload_size)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	*response_payload_size = 0;
	if (trustlet_status)
		*trustlet_status = 0;
	request_size = sizeof(request_header) + payload_size;
	request = calloc(1, request_size);
	response = calloc(1, FOCAL_TA_RESPONSE_BUFFER_SIZE);
	if (!request || !response)
		goto out;

	request_header.command = command;
	request_header.payload_length = (uint32_t)payload_size;
	memcpy(request, &request_header, sizeof(request_header));
	if (payload_size)
		memcpy(request + sizeof(request_header), payload, payload_size);

	result = protocol->exchange(protocol->context, request, request_size,
				    response, FOCAL_TA_RESPONSE_BUFFER_SIZE,
				    &response_size);
	if (result) {
		result = FOCAL_PROTOCOL_TRANSPORT_ERROR;
		goto out;
	}
	if (response_size < sizeof(response_header)) {
		result = FOCAL_PROTOCOL_RESPONSE_TRUNCATED;
		goto out;
	}
	memcpy(&response_header, response, sizeof(response_header));
	if (response_header.command !=
	    (command | FOCAL_TA_COMMAND_RESPONSE_BIT)) {
		result = FOCAL_PROTOCOL_RESPONSE_COMMAND;
		goto out;
	}
	if (response_header.payload_length >
	    response_size - sizeof(response_header)) {
		result = FOCAL_PROTOCOL_RESPONSE_LENGTH;
		goto out;
	}
	if (trustlet_status)
		*trustlet_status = response_header.status;
	else if (response_header.status) {
		result = FOCAL_PROTOCOL_TRUSTLET_ERROR;
		goto out;
	}
	if (response_header.payload_length > response_capacity) {
		result = FOCAL_PROTOCOL_CAPACITY;
		goto out;
	}
	if (response_header.payload_length)
		memcpy(response_payload, response + sizeof(response_header),
		       response_header.payload_length);
	*response_payload_size = response_header.payload_length;
	result = FOCAL_PROTOCOL_OK;

out:
	free(response);
	free(request);
	return result;
}

int focal_protocol_enumerate(const struct focal_protocol *protocol,
			     struct focal_template_id *templates,
			     size_t *template_count)
{
	uint8_t payload[sizeof(uint32_t) +
			FOCAL_TA_MAX_TEMPLATES * sizeof(struct focal_template_id)] = {};
	uint32_t requested;
	uint32_t returned;
	size_t payload_size;
	size_t required_size;
	int result;

	if (!template_count || *template_count > FOCAL_TA_MAX_TEMPLATES ||
	    (*template_count && !templates))
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	requested = (uint32_t)*template_count;
	result = exchange_command(protocol, FOCAL_TA_ENUMERATE, &requested,
				  sizeof(requested), payload, sizeof(payload),
				  &payload_size);
	if (result)
		return result;
	if (payload_size < sizeof(returned))
		return FOCAL_PROTOCOL_RESPONSE_LENGTH;
	returned = load_u32(payload);
	if (returned > FOCAL_TA_MAX_TEMPLATES)
		return FOCAL_PROTOCOL_RESPONSE_LENGTH;
	required_size = sizeof(returned) +
		(size_t)returned * sizeof(struct focal_template_id);
	if (payload_size != required_size)
		return FOCAL_PROTOCOL_RESPONSE_LENGTH;
	if (returned > requested)
		return FOCAL_PROTOCOL_CAPACITY;
	if (returned)
		memcpy(templates, payload + sizeof(returned),
		       (size_t)returned * sizeof(*templates));
	*template_count = returned;
	return FOCAL_PROTOCOL_OK;
}

int focal_protocol_set_active_group(const struct focal_protocol *protocol,
				    uint32_t group_id, const char *storage_path)

{
	int32_t status = 0;
	int result = focal_protocol_set_active_group_status(
		protocol, group_id, storage_path, &status);

	if (result)
		return result;
	return status ? FOCAL_PROTOCOL_TRUSTLET_ERROR : FOCAL_PROTOCOL_OK;
}

int focal_protocol_set_active_group_status(
	const struct focal_protocol *protocol, uint32_t group_id,
	const char *storage_path, int32_t *trustlet_status)
{
	uint8_t *request;
	size_t path_size;
	size_t request_size;
	size_t payload_size;
	int result;

	if (!storage_path || !trustlet_status)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	path_size = strlen(storage_path) + 1U;
	if (path_size <= 1U ||
	    path_size > FOCAL_TA_REQUEST_BUFFER_SIZE - sizeof(group_id))
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	request_size = sizeof(group_id) + path_size;
	request = calloc(1, request_size);
	if (!request)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	memcpy(request, &group_id, sizeof(group_id));
	memcpy(request + sizeof(group_id), storage_path, path_size);
	result = exchange_command_status(protocol, FOCAL_TA_SET_ACTIVE_GROUP,
					 request, request_size, NULL, 0,
					 &payload_size, trustlet_status);
	free(request);
	if (result)
		return result;
	return payload_size ? FOCAL_PROTOCOL_RESPONSE_LENGTH : FOCAL_PROTOCOL_OK;
}

int focal_protocol_sync_reset_statistics(const struct focal_protocol *protocol)
{
	static const size_t sentinel_offsets[] = {
		0xa8, 0xbc, 0xd0, 0xe4, 0xf8, 0x10c, 0x120, 0x134,
		0x148, 0x15c, 0x170, 0x184, 0x198, 0x1ac, 0x1c0,
		0x1d4, 0x1e8, 0x218,
	};
	uint8_t statistics[FOCAL_TA_STATISTICS_SIZE] = {};
	const int32_t unset = -1;
	size_t payload_size;
	size_t index;
	int result;

	/* Exact fresh-state image produced by QREL 16.95.0 ff_statistics_reset(). */
	for (index = 0; index < sizeof(sentinel_offsets) /
					 sizeof(sentinel_offsets[0]); index++)
		memcpy(statistics + sentinel_offsets[index], &unset,
		       sizeof(unset));
	result = exchange_command(protocol, FOCAL_TA_SYNC_STATISTICS,
				  statistics, sizeof(statistics), NULL, 0,
				  &payload_size);
	memset(statistics, 0, sizeof(statistics));
	if (result)
		return result;
	return payload_size ? FOCAL_PROTOCOL_RESPONSE_LENGTH : FOCAL_PROTOCOL_OK;
}

static bool protected_template_shape_valid(const uint8_t *blob, size_t size)
{
	uint32_t data_size;

	if (!blob || size < FOCAL_TA_PROTECTED_TEMPLATE_HEADER_SIZE ||
	    size > FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE)
		return false;
	data_size = load_u32(blob + 12U);
	return (size_t)data_size ==
		size - FOCAL_TA_PROTECTED_TEMPLATE_HEADER_SIZE;
}

int focal_protocol_export_protected_template(
	const struct focal_protocol *protocol, uint32_t group_id,
	uint32_t finger_id, uint8_t *protected_template,
	size_t *protected_template_size)
{
	uint32_t request[2] = { group_id, finger_id };
	size_t payload_size;
	int result;

	if (!finger_id || !protected_template || !protected_template_size ||
	    *protected_template_size < FOCAL_TA_PROTECTED_TEMPLATE_HEADER_SIZE ||
	    *protected_template_size > FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	result = exchange_command(protocol, FOCAL_TA_SYNC_TEMPLATE, request,
				  sizeof(request), protected_template,
				  *protected_template_size, &payload_size);
	if (result)
		return result;
	if (!protected_template_shape_valid(protected_template, payload_size))
		return FOCAL_PROTOCOL_RESPONSE_LENGTH;
	*protected_template_size = payload_size;
	return FOCAL_PROTOCOL_OK;
}

int focal_protocol_import_protected_template(
	const struct focal_protocol *protocol, uint32_t group_id,
	uint32_t finger_id, const uint8_t *protected_template,
	size_t protected_template_size)
{
	uint8_t *request = NULL;
	uint8_t *response = NULL;
	size_t request_size;
	size_t response_size = 0;
	int result = FOCAL_PROTOCOL_INVALID_ARGUMENT;

	if (!finger_id ||
	    !protected_template_shape_valid(protected_template,
					    protected_template_size) ||
	    protected_template_size >
		FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE - 2U * sizeof(uint32_t))
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	request_size = 2U * sizeof(uint32_t) + protected_template_size;
	request = calloc(1, request_size);
	response = calloc(1, FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE);
	if (!request || !response)
		goto out;
	memcpy(request, &group_id, sizeof(group_id));
	memcpy(request + sizeof(group_id), &finger_id, sizeof(finger_id));
	memcpy(request + 2U * sizeof(uint32_t), protected_template,
	       protected_template_size);
	result = exchange_command(protocol, FOCAL_TA_SYNC_TEMPLATE, request,
				  request_size, response,
				  FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE,
				  &response_size);
	if (!result && response_size != 0U &&
	    !protected_template_shape_valid(response, response_size))
		result = FOCAL_PROTOCOL_RESPONSE_LENGTH;
out:
	if (response) {
		memset(response, 0, FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE);
		free(response);
	}
	if (request) {
		memset(request, 0, request_size);
		free(request);
	}
	return result;
}

int focal_protocol_initialize(const struct focal_protocol *protocol,
			      uint8_t data[FOCAL_TA_INITIALIZATION_DATA_SIZE])
{
	size_t payload_size;
	int result;

	if (!data)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	result = exchange_command(protocol, FOCAL_TA_INITIALIZE, NULL, 0, data,
				  FOCAL_TA_INITIALIZATION_DATA_SIZE,
				  &payload_size);
	if (result)
		return result;
	return payload_size == FOCAL_TA_INITIALIZATION_DATA_SIZE ?
		FOCAL_PROTOCOL_OK : FOCAL_PROTOCOL_RESPONSE_LENGTH;
}

int focal_protocol_sync_config(const struct focal_protocol *protocol,
			       const char *json, size_t json_size)
{
	size_t payload_size;
	int result;

	if (!json || !json_size || json_size > FOCAL_TA_CONFIGURATION_MAX_SIZE ||
	    json[json_size - 1] != '\0')
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	result = exchange_command(protocol, FOCAL_TA_SYNC_CONFIG, json,
				  json_size, NULL, 0, &payload_size);
	if (result)
		return result;
	return payload_size ? FOCAL_PROTOCOL_RESPONSE_LENGTH : FOCAL_PROTOCOL_OK;
}

int focal_protocol_initialize_spi(const struct focal_protocol *protocol)
{
	return exchange_empty(protocol, FOCAL_TA_INITIALIZE_SPI);
}

int focal_protocol_free_spi(const struct focal_protocol *protocol)
{
	return exchange_empty(protocol, FOCAL_TA_FREE_SPI);
}

int focal_protocol_probe_device(const struct focal_protocol *protocol,
				int force,
				uint8_t data[FOCAL_TA_PROBE_DEVICE_DATA_SIZE])
{
	uint8_t request = force ? 1u : 0u;
	size_t payload_size;
	int result;

	if (!data)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	result = exchange_command(protocol, FOCAL_TA_PROBE_DEVICE, &request,
				  sizeof(request), data,
				  FOCAL_TA_PROBE_DEVICE_DATA_SIZE,
				  &payload_size);
	if (result)
		return result;
	return payload_size == FOCAL_TA_PROBE_DEVICE_DATA_SIZE ?
		FOCAL_PROTOCOL_OK : FOCAL_PROTOCOL_RESPONSE_LENGTH;
}

int focal_protocol_initialize_device(const struct focal_protocol *protocol,
				     uint8_t data[FOCAL_TA_DEVICE_DATA_SIZE])
{
	size_t payload_size;
	int result;

	if (!data)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	result = exchange_command(protocol, FOCAL_TA_INITIALIZE_DEVICE, NULL, 0,
				  data, FOCAL_TA_DEVICE_DATA_SIZE, &payload_size);
	if (result)
		return result;
	return payload_size == FOCAL_TA_DEVICE_DATA_SIZE ?
		FOCAL_PROTOCOL_OK : FOCAL_PROTOCOL_RESPONSE_LENGTH;
}

int focal_protocol_configure_work_mode(const struct focal_protocol *protocol,
				       uint32_t mode)
{
	size_t payload_size;
	int result;

	result = exchange_command(protocol, FOCAL_TA_CONFIGURE_WORK_MODE, &mode,
				  sizeof(mode), NULL, 0, &payload_size);
	if (result)
		return result;
	return payload_size ? FOCAL_PROTOCOL_RESPONSE_LENGTH : FOCAL_PROTOCOL_OK;
}

static int exchange_u64(const struct focal_protocol *protocol, uint32_t command,
			uint64_t *value)
{
	uint8_t payload[sizeof(uint64_t)] = {};
	size_t payload_size;
	int result;

	if (!value)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	result = exchange_command(protocol, command, NULL, 0, payload,
				  sizeof(payload), &payload_size);
	if (result)
		return result;
	if (payload_size != sizeof(uint64_t))
		return FOCAL_PROTOCOL_RESPONSE_LENGTH;
	*value = load_u64(payload);
	return FOCAL_PROTOCOL_OK;
}

static int exchange_empty(const struct focal_protocol *protocol,
			  uint32_t command)
{
	size_t payload_size;
	int result;

	result = exchange_command(protocol, command, NULL, 0, NULL, 0,
				  &payload_size);
	if (result)
		return result;
	return payload_size ? FOCAL_PROTOCOL_RESPONSE_LENGTH : FOCAL_PROTOCOL_OK;
}

int focal_protocol_pre_enroll(const struct focal_protocol *protocol,
			      uint64_t *challenge)
{
	return exchange_u64(protocol, FOCAL_TA_PRE_ENROLL, challenge);
}

int focal_protocol_enroll(
	const struct focal_protocol *protocol,
	const uint8_t hardware_auth_token[FOCAL_TA_HARDWARE_AUTH_TOKEN_SIZE],
	uint32_t *group_or_finger_id, uint8_t trusted_enrollment)
{
	uint8_t request[FOCAL_TA_ENROLL_REQUEST_SIZE] = {};
	uint8_t response[sizeof(uint32_t)] = {};
	size_t payload_size;
	int result;

	if (!hardware_auth_token || !group_or_finger_id ||
	    trusted_enrollment > 1u)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	memcpy(request, hardware_auth_token,
	       FOCAL_TA_HARDWARE_AUTH_TOKEN_SIZE);
	memcpy(request + FOCAL_TA_HARDWARE_AUTH_TOKEN_SIZE,
	       group_or_finger_id, sizeof(*group_or_finger_id));
	request[FOCAL_TA_HARDWARE_AUTH_TOKEN_SIZE +
		sizeof(*group_or_finger_id)] = trusted_enrollment;
	result = exchange_command(protocol, FOCAL_TA_ENROLL, request,
				  sizeof(request), response, sizeof(response),
				  &payload_size);
	if (result)
		return result;
	if (payload_size != sizeof(*group_or_finger_id))
		return FOCAL_PROTOCOL_RESPONSE_LENGTH;
	*group_or_finger_id = load_u32(response);
	return FOCAL_PROTOCOL_OK;
}

int focal_protocol_post_enroll(const struct focal_protocol *protocol)
{
	return exchange_empty(protocol, FOCAL_TA_POST_ENROLL);
}

static int query_semantic_status(const struct focal_protocol *protocol,
				 uint32_t command, uint32_t event,
				 int32_t *status)
{
	size_t payload_size;
	int result;

	if (!status)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	result = exchange_command_status(protocol, command, &event,
					 sizeof(event), NULL, 0,
					 &payload_size, status);
	if (result)
		return result;
	return payload_size ? FOCAL_PROTOCOL_RESPONSE_LENGTH :
		FOCAL_PROTOCOL_OK;
}

int focal_protocol_query_event_status(const struct focal_protocol *protocol,
				      uint32_t event, int32_t *status)
{
	return query_semantic_status(protocol, FOCAL_TA_QUERY_EVENT_STATUS,
				     event, status);
}

int focal_protocol_query_finger_status(const struct focal_protocol *protocol,
				       uint32_t event, int32_t *status)
{
	return query_semantic_status(protocol, FOCAL_TA_QUERY_FINGER_STATUS,
				     event, status);
}

int focal_protocol_capture_image(
	const struct focal_protocol *protocol,
	const uint8_t request[FOCAL_TA_CAPTURE_CONTEXT_SIZE],
	uint8_t response[FOCAL_TA_CAPTURE_CONTEXT_SIZE])
{
	int32_t status = 0;
	int result = focal_protocol_capture_image_status(protocol, request,
							 response, &status);

	if (result)
		return result;
	return status ? FOCAL_PROTOCOL_TRUSTLET_ERROR : FOCAL_PROTOCOL_OK;
}

int focal_protocol_capture_image_status(
	const struct focal_protocol *protocol,
	const uint8_t request[FOCAL_TA_CAPTURE_CONTEXT_SIZE],
	uint8_t response[FOCAL_TA_CAPTURE_CONTEXT_SIZE], int32_t *status)
{
	size_t payload_size;
	int result;

	if (!request || !response || !status)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	result = exchange_command_status(
		protocol, FOCAL_TA_CAPTURE_IMAGE, request,
		FOCAL_TA_CAPTURE_CONTEXT_SIZE, response,
		FOCAL_TA_CAPTURE_CONTEXT_SIZE, &payload_size, status);
	if (result)
		return result;
	if (*status && payload_size == 0)
		return FOCAL_PROTOCOL_OK;
	return payload_size == FOCAL_TA_CAPTURE_CONTEXT_SIZE ?
		FOCAL_PROTOCOL_OK : FOCAL_PROTOCOL_RESPONSE_LENGTH;
}

int focal_protocol_exchange_event(
	const struct focal_protocol *protocol,
	uint8_t context[FOCAL_TA_EVENT_CONTEXT_SIZE])
{
	int32_t status = 0;
	int result = focal_protocol_exchange_event_status(protocol, context,
							  &status);

	if (result)
		return result;
	return status ? FOCAL_PROTOCOL_TRUSTLET_ERROR : FOCAL_PROTOCOL_OK;
}

int focal_protocol_exchange_event_status(
	const struct focal_protocol *protocol,
	uint8_t context[FOCAL_TA_EVENT_CONTEXT_SIZE], int32_t *status)
{
	uint8_t response[FOCAL_TA_EVENT_CONTEXT_SIZE] = {};
	size_t payload_size;
	int result;

	if (!context || !status)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	result = exchange_command_status(
		protocol, FOCAL_TA_REPORT_EVENT, context,
		FOCAL_TA_EVENT_CONTEXT_SIZE, response, sizeof(response),
		&payload_size, status);
	if (result)
		return result;
	if (payload_size != sizeof(response))
		return FOCAL_PROTOCOL_RESPONSE_LENGTH;
	memcpy(context, response, sizeof(response));
	return FOCAL_PROTOCOL_OK;
}

int focal_protocol_report_event(
	const struct focal_protocol *protocol, uint32_t event,
	uint8_t context[FOCAL_TA_EVENT_CONTEXT_SIZE])
{
	uint8_t request[FOCAL_TA_EVENT_CONTEXT_SIZE] = {};
	size_t payload_size;
	int result;

	if (!context)
		return FOCAL_PROTOCOL_INVALID_ARGUMENT;
	/* Stock clears all 732 bytes and places the queried event at offset 4. */
	memcpy(request + sizeof(uint32_t), &event, sizeof(event));
	result = exchange_command(protocol, FOCAL_TA_REPORT_EVENT, request,
				  sizeof(request), context,
				  FOCAL_TA_EVENT_CONTEXT_SIZE,
				  &payload_size);
	if (result)
		return result;
	return payload_size == FOCAL_TA_EVENT_CONTEXT_SIZE ?
		FOCAL_PROTOCOL_OK : FOCAL_PROTOCOL_RESPONSE_LENGTH;
}

int focal_protocol_get_authenticator_id(const struct focal_protocol *protocol,
					uint64_t *authenticator_id)
{
	return exchange_u64(protocol, FOCAL_TA_GET_AUTHENTICATOR_ID,
			    authenticator_id);
}

int focal_protocol_cancel(const struct focal_protocol *protocol)
{
	return exchange_empty(protocol, FOCAL_TA_CANCEL);
}
