/* SPDX-License-Identifier: Apache-2.0 */

#ifndef LUMA_FP6_ENROLLMENT_PROTOCOL_H
#define LUMA_FP6_ENROLLMENT_PROTOCOL_H

#include "focal_protocol.h"

#include <stddef.h>
#include <stdint.h>

#define FP6_GATEKEEPER_ENROLL UINT32_C(0x00021001)
#define FP6_GATEKEEPER_VERIFY UINT32_C(0x00021002)
#define FP6_ENROLLMENT_MAX_GATEKEEPER_MESSAGE 1024U

enum fp6_enrollment_result {
	FP6_ENROLLMENT_OK = 0,
	FP6_ENROLLMENT_INVALID_ARGUMENT = -1,
	FP6_ENROLLMENT_GATEKEEPER_TRANSPORT = -2,
	FP6_ENROLLMENT_GATEKEEPER_STATUS = -3,
	FP6_ENROLLMENT_GATEKEEPER_PROTOCOL = -4,
	FP6_ENROLLMENT_FOCAL_PROTOCOL = -5,
	FP6_ENROLLMENT_TOKEN_MISMATCH = -6,
	FP6_ENROLLMENT_CAPACITY = -7,
};

typedef int (*fp6_gatekeeper_exchange_fn)(
	void *context, uint32_t command,
	const void *request, size_t request_size,
	void *response, size_t response_capacity, size_t *response_size,
	int32_t *secure_status);

struct fp6_enrollment_protocol {
	struct focal_protocol focal;
	fp6_gatekeeper_exchange_fn gatekeeper_exchange;
	void *gatekeeper_context;
};

struct fp6_enrollment_result_data {
	uint32_t template_id;
	size_t password_handle_size;
};

int fp6_enrollment_begin(
	const struct fp6_enrollment_protocol *protocol,
	uint32_t user_id, uint32_t group_id, uint8_t trusted_enrollment,
	const uint8_t *pin, size_t pin_size,
	uint8_t *password_handle, size_t password_handle_capacity,
	struct fp6_enrollment_result_data *result_data);

#endif
