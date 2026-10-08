// SPDX-License-Identifier: BSD-2-Clause
#include "qsee_supplicant.h"
#include "luma-fp6-listener-abi.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static uint64_t path_token(const uint8_t *path, size_t capacity)
{
	uint64_t value = UINT64_C(1469598103934665603);
	size_t index;

	for (index = 0; index < capacity && path[index] != '\0'; index++) {
		value ^= path[index];
		value *= UINT64_C(1099511628211);
	}
	return value;
}

static const struct qs_service *find_service(const struct qs_service *services,
					     size_t count, uint32_t id)
{
	for (size_t i = 0; i < count; i++)
		if (services[i].id == id)
			return &services[i];
	return NULL;
}

static int serve_luma_qcomtee(struct qs_store *store,
			      const struct qs_service *services, size_t count,
			      volatile sig_atomic_t *stop,
			      void (*ready)(void *data), void *ready_data)
{
	struct luma_fp6_listener_message message = {
		.abi_version = LUMA_FP6_LISTENER_ABI_VERSION,
	};
	void *payload;
	const struct qs_service *service;
	int fd, rc = -1, saved_errno;

	fd = open("/dev/luma-fp6-qsee-listener", O_RDWR | O_CLOEXEC);
	if (fd < 0)
		return -1;
	payload = calloc(1, 504U * 1024U);
	if (!payload) {
		close(fd);
		return -1;
	}
	for (size_t i = 0; i < count; i++)
		fprintf(stderr,
			"event=listener_registered transport=qseecomcompat id=%u size=%zu\n",
			services[i].id, services[i].buffer_size);
	if (ready)
		ready(ready_data);

	while (!*stop) {
		uint32_t service_command = 0;
		uint32_t request_count = 0;
		uint32_t request_backup = 0;
		uint32_t response_count = 0;
		int32_t request_offset = 0;
		int32_t service_result = 0;
		uint64_t request_path_token = 0;
		int dispatch_result;

		memset(&message, 0, sizeof(message));
		message.abi_version = LUMA_FP6_LISTENER_ABI_VERSION;
		message.payload_ptr = (uintptr_t)payload;
		message.payload_capacity = 504U * 1024U;
		if (ioctl(fd, LUMA_FP6_LISTENER_IOC_WAIT, &message)) {
			if (errno == EINTR && *stop) {
				rc = 0;
				break;
			}
			goto out;
		}
		service = find_service(services, count, message.listener_id);
		if (!service ||
		    message.abi_version != LUMA_FP6_LISTENER_ABI_VERSION ||
		    message.buffer_size != service->buffer_size) {
			errno = EPROTO;
			goto out;
		}
		if (message.buffer_size >= sizeof(service_command))
			memcpy(&service_command, payload, sizeof(service_command));
		/*
		 * GPFS read/write requests carry their byte count at +264.  Record
		 * only that metadata before dispatch, because dispatch overwrites the
		 * request header in place.  The reply count is at +8.  This lets a
		 * physical-device gate distinguish a real sealed-object transfer from
		 * an apparently successful zero-byte operation without exposing any
		 * path, credential, template, or payload content.
		 */
		if (message.listener_id == QS_GPFS_SERVICE_ID &&
		    (service_command % 4 == 0 || service_command % 4 == 1) &&
		    message.buffer_size >= 272) {
			memcpy(&request_count, (const uint8_t *)payload + 264,
			       sizeof(request_count));
			memcpy(&request_offset, (const uint8_t *)payload + 260,
			       sizeof(request_offset));
			memcpy(&request_backup, (const uint8_t *)payload + 268,
			       sizeof(request_backup));
			request_path_token = path_token((const uint8_t *)payload + 4,
						256);
		}
		dispatch_result = service->dispatch(store, payload,
						    message.buffer_size);
		message.dispatch_status = dispatch_result ? 1 : 0;
		if ((message.listener_id == QS_FS_SERVICE_ID ||
		     message.listener_id == QS_GPFS_SERVICE_ID) &&
		    message.buffer_size >= sizeof(service_command) +
					   sizeof(service_result)) {
			memcpy(&service_result,
			       (const uint8_t *)payload + sizeof(service_command),
			       sizeof(service_result));
			fprintf(stderr,
				"event=storage_request listener=%u command=0x%x "
				"dispatch=%d result=%d contents_logged=0\n",
				message.listener_id, service_command,
				dispatch_result, service_result);
			if (message.listener_id == QS_GPFS_SERVICE_ID &&
			    message.buffer_size >= 12) {
				memcpy(&response_count,
				       (const uint8_t *)payload + 8,
				       sizeof(response_count));
				fprintf(stderr,
					"event=gpfs_transfer command=0x%x "
					"requested=%u returned=%u error=%d offset=%d "
					"backup=%u path_token=%016llx "
					"contents_logged=0 paths_logged=0\n",
					service_command, request_count,
					response_count, service_result, request_offset,
					request_backup,
					(unsigned long long)request_path_token);
			}
		}
		if (ioctl(fd, LUMA_FP6_LISTENER_IOC_RESPOND, &message))
			goto out;
	}

out:
	saved_errno = errno;
	for (size_t i = 0; i < count; i++)
		if (services[i].reset)
			services[i].reset();
	memset(payload, 0, 504U * 1024U);
	free(payload);
	close(fd);
	if (*stop)
		return 0;
	errno = saved_errno;
	return rc;
}

const struct qs_transport qs_qseecom_transport = {
	.name = "qseecomcompat",
	.serve = serve_luma_qcomtee,
};
