/* SPDX-License-Identifier: Apache-2.0 */

#ifndef LUMA_FP6_FOCAL_PROTOCOL_H
#define LUMA_FP6_FOCAL_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define FOCAL_TA_COMMAND_RESPONSE_BIT UINT32_C(0x80000000)
#define FOCAL_QSEECOM_SHARED_BUFFER_SIZE UINT32_C(0x80040)
#define FOCAL_TA_REQUEST_BUFFER_SIZE UINT32_C(0x78000)
#define FOCAL_TA_RESPONSE_BUFFER_SIZE UINT32_C(0x8040)
#define FOCAL_TA_CONFIGURATION_MAX_SIZE UINT32_C(0x10000)
#define FOCAL_TA_MAX_TEMPLATES 32u
#define FOCAL_TA_INITIALIZATION_DATA_SIZE 192u
#define FOCAL_TA_DEVICE_DATA_SIZE 60u
#define FOCAL_TA_PROBE_DEVICE_DATA_SIZE FOCAL_TA_DEVICE_DATA_SIZE
#define FOCAL_TA_HARDWARE_AUTH_TOKEN_SIZE 69u
#define FOCAL_TA_ENROLL_REQUEST_SIZE 74u
#define FOCAL_TA_EVENT_CONTEXT_SIZE 732u
#define FOCAL_TA_CAPTURE_CONTEXT_SIZE 32u
#define FOCAL_TA_STATISTICS_SIZE 560u
#define FOCAL_TA_PROTECTED_TEMPLATE_HEADER_SIZE 16u
#define FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE 16312u

enum focal_ta_command {
	FOCAL_TA_SEGMENTED_TRANSFER = 0x1000,
	FOCAL_TA_INITIALIZE = 0x1004,
	FOCAL_TA_INITIALIZE_SPI = 0x1006,
	FOCAL_TA_FREE_SPI = 0x1007,
	FOCAL_TA_PROBE_DEVICE = 0x100a,
	FOCAL_TA_INITIALIZE_DEVICE = 0x100b,
	FOCAL_TA_SYNC_CONFIG = 0x100d,
	FOCAL_TA_SYNC_STATISTICS = 0x100e,
	FOCAL_TA_SYNC_TEMPLATE = 0x1010,
	FOCAL_TA_CAPTURE_IMAGE = 0x1013,
	FOCAL_TA_REPORT_EVENT = 0x1018,
	FOCAL_TA_QUERY_EVENT_STATUS = 0x101d,
	FOCAL_TA_QUERY_FINGER_STATUS = 0x101e,
	FOCAL_TA_CONFIGURE_WORK_MODE = 0x1020,
	FOCAL_TA_PRE_ENROLL = 0x2000,
	FOCAL_TA_ENROLL = 0x2001,
	FOCAL_TA_POST_ENROLL = 0x2002,
	FOCAL_TA_GET_AUTHENTICATOR_ID = 0x2003,
	FOCAL_TA_CANCEL = 0x2004,
	FOCAL_TA_ENUMERATE = 0x2005,
	FOCAL_TA_SET_ACTIVE_GROUP = 0x2007,
};

enum focal_protocol_result {
	FOCAL_PROTOCOL_OK = 0,
	FOCAL_PROTOCOL_INVALID_ARGUMENT = -1,
	FOCAL_PROTOCOL_TRANSPORT_ERROR = -2,
	FOCAL_PROTOCOL_RESPONSE_TRUNCATED = -3,
	FOCAL_PROTOCOL_RESPONSE_COMMAND = -4,
	FOCAL_PROTOCOL_RESPONSE_LENGTH = -5,
	FOCAL_PROTOCOL_TRUSTLET_ERROR = -6,
	FOCAL_PROTOCOL_CAPACITY = -7,
};

struct focal_template_id {
	uint32_t group_id;
	uint32_t finger_id;
};

typedef int (*focal_exchange_fn)(void *context,
				 const void *request, size_t request_size,
				 void *response, size_t response_capacity,
				 size_t *response_size);

struct focal_protocol {
	focal_exchange_fn exchange;
	void *context;
};

int focal_protocol_initialize(const struct focal_protocol *protocol,
			      uint8_t data[FOCAL_TA_INITIALIZATION_DATA_SIZE]);
int focal_protocol_sync_config(const struct focal_protocol *protocol,
			       const char *json, size_t json_size);
int focal_protocol_initialize_spi(const struct focal_protocol *protocol);
int focal_protocol_free_spi(const struct focal_protocol *protocol);
int focal_protocol_probe_device(const struct focal_protocol *protocol,
				int force,
				uint8_t data[FOCAL_TA_PROBE_DEVICE_DATA_SIZE]);
int focal_protocol_initialize_device(const struct focal_protocol *protocol,
				     uint8_t data[FOCAL_TA_DEVICE_DATA_SIZE]);
int focal_protocol_configure_work_mode(const struct focal_protocol *protocol,
				       uint32_t mode);
int focal_protocol_enumerate(const struct focal_protocol *protocol,
			     struct focal_template_id *templates,
			     size_t *template_count);
int focal_protocol_set_active_group(const struct focal_protocol *protocol,
				    uint32_t group_id, const char *storage_path);
int focal_protocol_set_active_group_status(
	const struct focal_protocol *protocol, uint32_t group_id,
	const char *storage_path, int32_t *trustlet_status);
int focal_protocol_sync_reset_statistics(const struct focal_protocol *protocol);
int focal_protocol_export_protected_template(
	const struct focal_protocol *protocol, uint32_t group_id,
	uint32_t finger_id, uint8_t *protected_template,
	size_t *protected_template_size);
int focal_protocol_import_protected_template(
	const struct focal_protocol *protocol, uint32_t group_id,
	uint32_t finger_id, const uint8_t *protected_template,
	size_t protected_template_size);
int focal_protocol_pre_enroll(const struct focal_protocol *protocol,
			      uint64_t *challenge);
int focal_protocol_enroll(
	const struct focal_protocol *protocol,
	const uint8_t hardware_auth_token[FOCAL_TA_HARDWARE_AUTH_TOKEN_SIZE],
	uint32_t *group_or_finger_id, uint8_t trusted_enrollment);
int focal_protocol_post_enroll(const struct focal_protocol *protocol);
int focal_protocol_query_event_status(const struct focal_protocol *protocol,
				      uint32_t event, int32_t *status);
int focal_protocol_query_finger_status(const struct focal_protocol *protocol,
				       uint32_t event, int32_t *status);
int focal_protocol_capture_image(
	const struct focal_protocol *protocol,
	const uint8_t request[FOCAL_TA_CAPTURE_CONTEXT_SIZE],
	uint8_t response[FOCAL_TA_CAPTURE_CONTEXT_SIZE]);
int focal_protocol_capture_image_status(
	const struct focal_protocol *protocol,
	const uint8_t request[FOCAL_TA_CAPTURE_CONTEXT_SIZE],
	uint8_t response[FOCAL_TA_CAPTURE_CONTEXT_SIZE], int32_t *status);
int focal_protocol_exchange_event(
	const struct focal_protocol *protocol,
	uint8_t context[FOCAL_TA_EVENT_CONTEXT_SIZE]);
int focal_protocol_exchange_event_status(
	const struct focal_protocol *protocol,
	uint8_t context[FOCAL_TA_EVENT_CONTEXT_SIZE], int32_t *status);
int focal_protocol_report_event(
	const struct focal_protocol *protocol, uint32_t event,
	uint8_t context[FOCAL_TA_EVENT_CONTEXT_SIZE]);
int focal_protocol_get_authenticator_id(const struct focal_protocol *protocol,
					uint64_t *authenticator_id);
int focal_protocol_cancel(const struct focal_protocol *protocol);

#endif
