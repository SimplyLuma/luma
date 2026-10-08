/* SPDX-License-Identifier: Apache-2.0 */

#include "enrollment_protocol.h"
#include "focal_driver_control.h"
#include "focal_protocol.h"
#include "luma-fp6-enrollment-abi.h"
#include "../fp6-fingerprint-qsee-bridge/QSEEComAPI.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <security/pam_appl.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define FP6_ENROLLMENT_DEVICE "/dev/luma-fp6-fingerprint"
#define FP6_FOCAL_DEVICE "/dev/focaltech_fp"
#define FP6_CREDENTIAL_DIRECTORY "/var/lib/luma/fingerprint"
#define FP6_ACTIVE_GROUP_PATH "/data/vendor_de/0/fpdata"
#define FP6_CREDENTIAL_PATH FP6_CREDENTIAL_DIRECTORY "/credential.handle"
#define FP6_PROTECTED_TEMPLATE_PATH \
	FP6_CREDENTIAL_DIRECTORY "/template.protected"
#define FP6_PIN_CAPACITY 64U
#define FP6_HANDLE_CAPACITY 512U
#define FP6_EVENT_TIMEOUT_MS 15000
#define FP6_MAX_EVENT_CYCLES 120U
#define FP6_ENROLLMENT_BURST_SAMPLES 5U
#define FP6_EVENT_INTERRUPT 1
#define FP6_SECURE_RETRY (-11)
#define FP6_SECURE_BUSY (-16)
#define FP6_GATEKEEPER_DELETE_USER UINT32_C(0x00021003)
#define FP6_GATEKEEPER_DELETE_ALL_USERS UINT32_C(0x00021004)
#define FP6_LSKF_SALT_SIZE 16U
#define FP6_FOCAL_QSEECOM_BUFFER_SIZE UINT32_C(0x80040)

struct focal_message_header {
	uint32_t command;
	uint32_t payload_length;
	int32_t status;
	uint32_t flags;
};

struct fp6_transport {
	int secure_fd;
	uint32_t last_service;
	uint32_t last_command;
	int32_t last_secure_status;
	size_t last_response_size;
	int last_errno;
};

struct fp6_qsee_transport {
	struct QSEECom_handle *handle;
};

struct fp6_enrollment_state {
	uint32_t capture_count;
	uint32_t report_count;
};

struct fp6_credential_record {
	uint8_t magic[8];
	uint32_t version;
	uint32_t user_id;
	uint32_t group_id;
	uint32_t template_id;
	uint32_t handle_size;
	uint32_t scrypt_log_n;
	uint32_t scrypt_log_r;
	uint32_t scrypt_log_p;
	uint8_t salt[FP6_LSKF_SALT_SIZE];
	uint8_t handle[FP6_HANDLE_CAPACITY];
};

struct fp6_protected_template_record {
	uint8_t magic[8];
	uint32_t version;
	uint32_t group_id;
	uint32_t finger_id;
	uint32_t blob_size;
};

struct fp6_pam_secret {
	const uint8_t *data;
	size_t size;
};

static volatile sig_atomic_t interrupted;

static void secure_clear(void *data, size_t size)
{
	volatile uint8_t *cursor = data;

	while (size--)
		*cursor++ = 0;
}

static int pam_secret_conversation(int message_count,
				   const struct pam_message **messages,
				   struct pam_response **responses,
				   void *opaque)
{
	const struct fp6_pam_secret *secret = opaque;
	struct pam_response *reply;

	if (!secret || !secret->data || !secret->size || message_count <= 0 ||
	    !messages || !responses)
		return PAM_CONV_ERR;
	reply = calloc((size_t)message_count, sizeof(*reply));
	if (!reply)
		return PAM_BUF_ERR;
	for (int index = 0; index < message_count; index++) {
		if (!messages[index])
			goto fail;
		if (messages[index]->msg_style == PAM_PROMPT_ECHO_OFF) {
			reply[index].resp = calloc(secret->size + 1U, 1U);
			if (!reply[index].resp)
				goto fail;
			memcpy(reply[index].resp, secret->data, secret->size);
		} else if (messages[index]->msg_style != PAM_TEXT_INFO &&
			   messages[index]->msg_style != PAM_ERROR_MSG) {
			goto fail;
		}
	}
	*responses = reply;
	return PAM_SUCCESS;

fail:
	for (int index = 0; index < message_count; index++) {
		if (reply[index].resp) {
			secure_clear(reply[index].resp,
				     strlen(reply[index].resp));
			free(reply[index].resp);
		}
	}
	free(reply);
	return PAM_CONV_ERR;
}

static int authenticate_luma_pin(const uint8_t *pin, size_t pin_size)
{
	struct fp6_pam_secret secret = { .data = pin, .size = pin_size };
	struct pam_conv conversation = {
		.conv = pam_secret_conversation,
		.appdata_ptr = &secret,
	};
	pam_handle_t *handle = NULL;
	int result;

	result = pam_start("login", "luma", &conversation, &handle);
	if (result == PAM_SUCCESS)
		result = pam_authenticate(handle, PAM_SILENT);
	if (handle)
		(void)pam_end(handle, result);
	return result == PAM_SUCCESS ? 0 : -1;
}

static void handle_signal(int signal_number)
{
	(void)signal_number;
	interrupted = 1;
}

static uint32_t load_u32(const uint8_t *source)
{
	uint32_t value;

	memcpy(&value, source, sizeof(value));
	return value;
}

/*
 * The qcomtee enrollment module owns the signed-TA load and sensor setup.  Once
 * the stock listeners are registered, biometric commands must use this TEE
 * client path so secure-world filesystem/RPMB callbacks can be serviced.
 */
static int qsee_focal_exchange(void *opaque, const void *request,
			       size_t request_size, void *response,
			       size_t response_capacity, size_t *response_size)
{
	struct fp6_qsee_transport *transport = opaque;
	uint32_t payload_size;

	if (!transport || !transport->handle || !request || !request_size ||
	    !response || response_capacity < sizeof(struct focal_message_header) ||
	    !response_size || request_size > UINT32_MAX ||
	    response_capacity > UINT32_MAX) {
		errno = EINVAL;
		return -1;
	}
	if (QSEECom_send_cmd(transport->handle, (void *)request,
			     (uint32_t)request_size, response,
			     (uint32_t)response_capacity))
		return -1;
	payload_size = load_u32((const uint8_t *)response +
				sizeof(uint32_t));
	if (payload_size > response_capacity -
				   sizeof(struct focal_message_header)) {
		errno = EPROTO;
		return -1;
	}
	*response_size = sizeof(struct focal_message_header) + payload_size;
	return 0;
}

static void store_u32(uint8_t *destination, uint32_t value)
{
	memcpy(destination, &value, sizeof(value));
}

static size_t focal_response_capacity(uint32_t command)
{
	switch (command) {
	case FOCAL_TA_PRE_ENROLL:
		return sizeof(uint64_t);
	case FOCAL_TA_ENROLL:
		return sizeof(uint32_t);
	case FOCAL_TA_POST_ENROLL:
	case FOCAL_TA_CANCEL:
	case FOCAL_TA_QUERY_EVENT_STATUS:
	case FOCAL_TA_QUERY_FINGER_STATUS:
	case FOCAL_TA_CONFIGURE_WORK_MODE:
	case FOCAL_TA_SET_ACTIVE_GROUP:
	case FOCAL_TA_SYNC_STATISTICS:
		return 0;
	case FOCAL_TA_SYNC_TEMPLATE:
		return FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE;
	case FOCAL_TA_ENUMERATE:
		return sizeof(uint32_t) +
			FOCAL_TA_MAX_TEMPLATES * sizeof(struct focal_template_id);
	case FOCAL_TA_CAPTURE_IMAGE:
		return FOCAL_TA_CAPTURE_CONTEXT_SIZE;
	case FOCAL_TA_REPORT_EVENT:
		return FOCAL_TA_EVENT_CONTEXT_SIZE;
	default:
		return SIZE_MAX;
	}
}

static int secure_ioctl_exchange(
	struct fp6_transport *transport, uint32_t service, uint32_t command,
	const void *request, size_t request_size, size_t response_capacity,
	void *response, size_t *response_size, int32_t *secure_status)
{
	struct luma_fp6_secure_exchange *exchange;
	int result = -1;

	if (!transport || transport->secure_fd < 0 ||
	    request_size > LUMA_FP6_ENROLLMENT_MAX_PAYLOAD ||
	    response_capacity > LUMA_FP6_ENROLLMENT_MAX_PAYLOAD ||
	    (request_size && !request) || (response_capacity && !response) ||
	    !response_size || !secure_status) {
		errno = EINVAL;
		return -1;
	}
	transport->last_service = service;
	transport->last_command = command;
	transport->last_secure_status = 0;
	transport->last_response_size = 0;
	transport->last_errno = 0;
	exchange = calloc(1, sizeof(*exchange));
	if (!exchange)
		return -1;
	exchange->abi_version = LUMA_FP6_ENROLLMENT_ABI_VERSION;
	exchange->service = service;
	exchange->command = command;
	exchange->request_size = (uint32_t)request_size;
	exchange->response_capacity = (uint32_t)response_capacity;
	if (request_size)
		memcpy(exchange->payload, request, request_size);
	if (ioctl(transport->secure_fd, LUMA_FP6_ENROLLMENT_IOC_EXCHANGE,
		  exchange) < 0) {
		transport->last_errno = errno;
		goto out;
	}
	if (exchange->response_size > response_capacity) {
		errno = EPROTO;
		goto out;
	}
	if (exchange->response_size)
		memcpy(response, exchange->payload, exchange->response_size);
	*response_size = exchange->response_size;
	*secure_status = exchange->secure_status;
	transport->last_response_size = exchange->response_size;
	transport->last_secure_status = exchange->secure_status;
	result = 0;
out:
	secure_clear(exchange, sizeof(*exchange));
	free(exchange);
	return result;
}

static int gatekeeper_exchange(
	void *opaque, uint32_t command, const void *request, size_t request_size,
	void *response, size_t response_capacity, size_t *response_size,
	int32_t *secure_status)
{
	return secure_ioctl_exchange(opaque, LUMA_FP6_SERVICE_GATEKEEPER,
				     command, request, request_size,
				     response_capacity, response,
				     response_size, secure_status);
}

static int focal_exchange(void *opaque, const void *request,
			  size_t request_size, void *response,
			  size_t response_capacity, size_t *response_size)
{
	const struct focal_message_header *header = request;
	struct focal_message_header response_header = {};
	size_t payload_capacity;
	size_t payload_size = 0;
	int32_t secure_status = 0;

	if (!request || request_size < sizeof(*header) || !response ||
	    !response_size ||
	    header->payload_length != request_size - sizeof(*header)) {
		errno = EINVAL;
		return -1;
	}
	payload_capacity = focal_response_capacity(header->command);
	if (payload_capacity == SIZE_MAX ||
	    sizeof(response_header) + payload_capacity > response_capacity) {
		errno = EINVAL;
		return -1;
	}
	if (secure_ioctl_exchange(
		    opaque, LUMA_FP6_SERVICE_FOCAL, header->command,
		    (const uint8_t *)request + sizeof(*header),
		    header->payload_length, payload_capacity,
		    (uint8_t *)response + sizeof(response_header), &payload_size,
		    &secure_status))
		return -1;
	response_header.command = header->command |
		FOCAL_TA_COMMAND_RESPONSE_BIT;
	response_header.payload_length = (uint32_t)payload_size;
	response_header.status = secure_status;
	memcpy(response, &response_header, sizeof(response_header));
	*response_size = sizeof(response_header) + payload_size;
	return 0;
}

static int read_pin(uint8_t pin[FP6_PIN_CAPACITY], size_t *pin_size)
{
	struct termios original;
	struct termios hidden;
	bool terminal = isatty(STDIN_FILENO);
	char buffer[FP6_PIN_CAPACITY + 2U] = {};
	size_t length;

	if (!pin || !pin_size)
		return -1;
	if (terminal) {
		if (tcgetattr(STDIN_FILENO, &original))
			return -1;
		hidden = original;
		hidden.c_lflag &= (tcflag_t)~ECHO;
		if (tcsetattr(STDIN_FILENO, TCSAFLUSH, &hidden))
			return -1;
		fputs("Luma PIN: ", stderr);
		fflush(stderr);
	}
	if (!fgets(buffer, sizeof(buffer), stdin)) {
		if (terminal)
			(void)tcsetattr(STDIN_FILENO, TCSAFLUSH, &original);
		return -1;
	}
	if (terminal) {
		(void)tcsetattr(STDIN_FILENO, TCSAFLUSH, &original);
		fputc('\n', stderr);
	}
	length = strcspn(buffer, "\r\n");
	if (!length || length > FP6_PIN_CAPACITY ||
	    (buffer[length] != '\r' && buffer[length] != '\n')) {
		secure_clear(buffer, sizeof(buffer));
		errno = EINVAL;
		return -1;
	}
	memcpy(pin, buffer, length);
	*pin_size = length;
	secure_clear(buffer, sizeof(buffer));
	return 0;
}

static int verify_private_directory(const char *path)
{
	struct stat status;

	if (lstat(path, &status)) {
		if (errno != ENOENT || mkdir(path, 0700))
			return -1;
		if (lstat(path, &status))
			return -1;
	}
	if (!S_ISDIR(status.st_mode) || S_ISLNK(status.st_mode) ||
	    status.st_uid != 0 || (status.st_mode & 0777U) != 0700U) {
		errno = EPERM;
		return -1;
	}
	return 0;
}

static int persist_credential(uint32_t user_id, uint32_t group_id,
			      uint32_t template_id,
			      const uint8_t salt[FP6_LSKF_SALT_SIZE],
			      const uint8_t *handle,
			      size_t handle_size)
{
	static const uint8_t magic[8] = { 'L', 'U', 'M', 'A', 'F', 'P', '6', 0 };
	const char *temporary = FP6_CREDENTIAL_DIRECTORY "/.credential.handle.new";
	struct fp6_credential_record record = {};
	ssize_t written;
	int descriptor = -1;
	int result = -1;

	if (!handle || !handle_size || handle_size > sizeof(record.handle)) {
		errno = EINVAL;
		return -1;
	}
	if (verify_private_directory("/var/lib/luma") ||
	    verify_private_directory(FP6_CREDENTIAL_DIRECTORY))
		return -1;
	memcpy(record.magic, magic, sizeof(magic));
	record.version = 2;
	record.user_id = user_id;
	record.group_id = group_id;
	record.template_id = template_id;
	record.handle_size = (uint32_t)handle_size;
	record.scrypt_log_n = 11;
	record.scrypt_log_r = 3;
	record.scrypt_log_p = 1;
	memcpy(record.salt, salt, sizeof(record.salt));
	memcpy(record.handle, handle, handle_size);
	descriptor = open(temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW,
			  0600);
	if (descriptor < 0)
		goto out;
	written = write(descriptor, &record,
			offsetof(struct fp6_credential_record, handle) + handle_size);
	if (written != (ssize_t)(offsetof(struct fp6_credential_record, handle) +
				 handle_size) || fsync(descriptor) || close(descriptor)) {
		descriptor = -1;
		goto out;
	}
	descriptor = -1;
	if (rename(temporary, FP6_CREDENTIAL_PATH))
		goto out;
	result = 0;
out:
	if (descriptor >= 0)
		(void)close(descriptor);
	if (result)
		(void)unlink(temporary);
	secure_clear(&record, sizeof(record));
	return result;
}

static int persist_protected_template(uint32_t group_id, uint32_t finger_id,
				      const uint8_t *blob, size_t blob_size)
{
	static const uint8_t magic[8] = { 'L', 'U', 'M', 'A', 'F', 'P', 'T', 0 };
	const char *temporary =
		FP6_CREDENTIAL_DIRECTORY "/.template.protected.new";
	struct fp6_protected_template_record header = {};
	int descriptor = -1;
	int directory = -1;
	int result = -1;
	ssize_t written;

	if (!finger_id || !blob ||
	    blob_size < FOCAL_TA_PROTECTED_TEMPLATE_HEADER_SIZE ||
	    blob_size > FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE) {
		errno = EINVAL;
		return -1;
	}
	if (verify_private_directory("/var/lib/luma") ||
	    verify_private_directory(FP6_CREDENTIAL_DIRECTORY))
		return -1;
	memcpy(header.magic, magic, sizeof(magic));
	header.version = 1U;
	header.group_id = group_id;
	header.finger_id = finger_id;
	header.blob_size = (uint32_t)blob_size;
	descriptor = open(temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW,
			  0600);
	if (descriptor < 0)
		goto out;
	written = write(descriptor, &header, sizeof(header));
	if (written != (ssize_t)sizeof(header))
		goto out;
	written = write(descriptor, blob, blob_size);
	if (written != (ssize_t)blob_size || fsync(descriptor) ||
	    close(descriptor)) {
		descriptor = -1;
		goto out;
	}
	descriptor = -1;
	if (rename(temporary, FP6_PROTECTED_TEMPLATE_PATH))
		goto out;
	directory = open(FP6_CREDENTIAL_DIRECTORY,
			 O_RDONLY | O_CLOEXEC | O_DIRECTORY | O_NOFOLLOW);
	if (directory < 0 || fsync(directory))
		goto out;
	result = 0;
out:
	if (directory >= 0)
		(void)close(directory);
	if (descriptor >= 0)
		(void)close(descriptor);
	if (result)
		(void)unlink(temporary);
	secure_clear(&header, sizeof(header));
	return result;
}

static int wait_for_interrupt(int focal_fd)
{
	struct pollfd descriptor = {
		.fd = focal_fd,
		.events = POLLIN,
	};
	int event_info = 0;
	int result;

	do {
		result = poll(&descriptor, 1, FP6_EVENT_TIMEOUT_MS);
	} while (result < 0 && errno == EINTR && !interrupted);
	if (result <= 0)
		return result;
	if (!(descriptor.revents & POLLIN)) {
		errno = EIO;
		return -1;
	}
	if (ioctl(focal_fd, FOCAL_IOCTL_GET_EVENT_INFO, &event_info))
		return -1;
	if (!(event_info & FP6_EVENT_INTERRUPT)) {
		errno = EAGAIN;
		return -1;
	}
	return 1;
}

static int focal_device_ioctl(void *opaque, uint32_t request,
			      uintptr_t argument)
{
	int descriptor = *(const int *)opaque;

	if (ioctl(descriptor, request, argument) < 0)
		return -errno;
	return 0;
}

static int focal_device_sleep(void *opaque, unsigned int milliseconds)
{
	struct timespec remaining = {
		.tv_sec = (time_t)(milliseconds / 1000U),
		.tv_nsec = (long)(milliseconds % 1000U) * 1000000L,
	};

	(void)opaque;
	while (nanosleep(&remaining, &remaining) < 0) {
		if (errno != EINTR)
			return -errno;
	}
	return 0;
}

static int enrollment_capture_cycle(const struct focal_protocol *focal,
				    const struct focal_driver_transport *driver,
				    struct fp6_enrollment_state *state,
				    uint32_t *remaining)
{
	uint8_t event[FOCAL_TA_EVENT_CONTEXT_SIZE] = {};
	int32_t status = 0;
	int result;

	/*
	 * QREL's exact stock configuration requests five acquisition passes while
	 * the finger remains down (device.sliding_enroll_burst_num == 5), followed
	 * by one report. Reporting every individual pass makes the TA repeatedly
	 * return the first-sample 80% result and never accumulate a template.
	 */
	/* Stock performs enable_prev_hw_process once at the start of the burst. */
	result = focal_driver_prepare_capture(driver);
	if (result)
		return result;
	for (uint32_t pass = 0; pass < FP6_ENROLLMENT_BURST_SAMPLES; pass++) {
		uint8_t capture[FOCAL_TA_CAPTURE_CONTEXT_SIZE] = {};
		uint8_t capture_response[FOCAL_TA_CAPTURE_CONTEXT_SIZE] = {};
		int32_t finger_status = 0;

		/* Only the first capture carries the previous-hardware-process bit. */
		store_u32(capture, pass == 0U ? 1U : 0U);
		store_u32(capture + 4, 0);
		/* Stock clears capture_context.word2 before every acquisition. */
		store_u32(capture + 8, 0);
		store_u32(capture + 12, state->capture_count);
		store_u32(capture + 16, 1);
		store_u32(capture + 20, pass);
		store_u32(capture + 24, UINT32_C(0xc0040002));
		result = focal_protocol_capture_image_status(
			focal, capture, capture_response, &status);
		if (result)
			return result;
		if (status == FP6_SECURE_BUSY || status == FP6_SECURE_RETRY)
			return 1;
		if (status != 0)
			return -1000 + status;
		state->capture_count = load_u32(capture_response + 12);

		result = focal_protocol_query_finger_status(focal, 1,
							    &finger_status);
		if (result)
			return result;
		if (finger_status == 0)
			break;
	}

	store_u32(event + 4, state->report_count ? 7U : 5U);
	/* QREL carries capture_context.word3 into event_context.word178. */
	store_u32(event + 0x2c8, state->capture_count);
	store_u32(event + 0x2d0, state->report_count);
	/*
	 * QREL's five-sample click-enrollment mode uses only the base event bits
	 * plus the non-gesture marker. The 0xc0040000 bits belong to the distinct
	 * one-sample path and prevent this configuration from accumulating.
	 */
	store_u32(event + 0x2d4, UINT32_C(0x08080004));
	result = focal_protocol_exchange_event_status(focal, event, &status);
	if (result)
		return result;
	if (status == FP6_SECURE_RETRY) {
		state->report_count++;
		*remaining = load_u32(event + 36);
		return 2;
	}
	if (status != 0 && status != FP6_SECURE_RETRY)
		return -2000 + status;
	/*
	 * These are the stock HAL's non-sensitive enrollment state fields.  Keep
	 * the opaque capture/event payload private; exposing only the semantic
	 * result lets the physical test distinguish continuation from completion.
	 */
	printf("Enrollment report: secure_status=%" PRId32
	       " state=%" PRIu32 " kind=%" PRIu32
	       " finger=%" PRIu32 " group=%" PRIu32
	       " acquired=%" PRIu32 " remaining=%" PRIu32 "\n",
	       status, load_u32(event), load_u32(event + 4),
	       load_u32(event + 12), load_u32(event + 16),
	       load_u32(event + 20), load_u32(event + 36));
	state->report_count++;
	*remaining = load_u32(event + 36);
	/*
	 * Zero remaining means that the acquisition quota is full, but it is not
	 * the stock commit boundary.  QREL continues through one more physical
	 * capture/report cycle and accepts completion only when ff_trustlet_event
	 * returns -11.  Stopping here leaves the secure template uncommitted even
	 * though every requested sample has been collected.
	 */
	if (load_u32(event) == 0U && *remaining == 0U)
		return 3;
	return 0;
}

static int enrollment_report_release(const struct focal_protocol *focal)
{
	uint8_t event[FOCAL_TA_EVENT_CONTEXT_SIZE] = {};
	int32_t status = 0;
	int result;

	/* QREL event-status 6 dispatches an otherwise-zero event context type 6. */
	store_u32(event + 4, 6);
	result = focal_protocol_exchange_event_status(focal, event, &status);
	if (result)
		return result;
	return status == 0 ? 0 : -3000 + status;
}

static int enumerate_one(const struct focal_protocol *focal,
			 struct focal_template_id *template)
{
	size_t count = 1;
	int result = focal_protocol_enumerate(focal, template, &count);

	if (result)
		return result;
	return count == 1 ? 1 : 0;
}

int main(int argc, char **argv)
{
	struct sigaction action = { .sa_handler = handle_signal };
	struct fp6_transport transport = { .secure_fd = -1 };
	struct fp6_qsee_transport qsee_transport = {};
	struct fp6_enrollment_protocol protocol = {
		.focal = { .exchange = focal_exchange, .context = &transport },
		.gatekeeper_exchange = gatekeeper_exchange,
		.gatekeeper_context = &transport,
	};
	struct fp6_enrollment_result_data begin = {};
	struct fp6_enrollment_state state = { .capture_count = 1 };
	struct focal_template_id enrolled_template = {};
	uint8_t pin[FP6_PIN_CAPACITY] = {};
	uint8_t lskf_salt[FP6_LSKF_SALT_SIZE] = {};
	uint8_t password_handle[FP6_HANDLE_CAPACITY] = {};
	uint8_t *protected_template = NULL;
	size_t protected_template_size = 0;
	size_t pin_size = 0;
	/* Biometric HATs are bound to the real primary-user SID, not fake LSKF UID. */
	const uint32_t user_id = 0;
	const uint32_t group_id = 0;
	bool enrollment_started = false;
	bool enrollment_completed = false;
	bool enrollment_work_mode = false;
	bool post_reset = false;
	bool post_user_reset = false;
	bool linux_native = false;
	bool qsee_focal = false;
	int focal_fd = -1;
	struct focal_driver_transport driver = {
		.ioctl = focal_device_ioctl,
		.sleep_ms = focal_device_sleep,
		.context = &focal_fd,
	};
	int exit_status = EXIT_FAILURE;

	if (argc == 2 && strcmp(argv[1], "--linux-native") == 0) {
		linux_native = true;
		qsee_focal = true;
	} else if (argc == 2 &&
		   strcmp(argv[1], "--linux-native-qcomtee") == 0) {
		/*
		 * focal64 can be resident behind the smcinvoke transport while the
		 * independently registered stock QSEE listeners service its callbacks.
		 * Keep biometric commands on the identity-checked qcomtee session in
		 * that topology; the legacy QSEE app-send ABI cannot address an app
		 * loaded through smcinvoke even when its numeric app ID is known.
		 */
		linux_native = true;
	}
	else if (argc == 2 && strcmp(argv[1], "--post-reset") == 0)
		post_reset = true;
	else if (argc == 2 && strcmp(argv[1], "--post-user-reset") == 0) {
		post_reset = true;
		post_user_reset = true;
	} else if (argc != 1) {
		fputs("usage: fp6-fingerprint-enroll [--linux-native|--linux-native-qcomtee|--post-reset|--post-user-reset]\n",
		      stderr);
		return EXIT_FAILURE;
	}

	(void)sigemptyset(&action.sa_mask);
	(void)sigaction(SIGINT, &action, NULL);
	(void)sigaction(SIGTERM, &action, NULL);
	transport.secure_fd = open(FP6_ENROLLMENT_DEVICE,
				   O_RDWR | O_CLOEXEC | O_NOFOLLOW);
	if (transport.secure_fd < 0) {
		perror("open enrollment transport");
		goto out;
	}
	focal_fd = open(FP6_FOCAL_DEVICE, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
	if (focal_fd < 0) {
		perror("open fingerprint device");
		goto out;
	}
	if (qsee_focal) {
		if (QSEECom_start_app(&qsee_transport.handle, NULL, "focal64",
				      FP6_FOCAL_QSEECOM_BUFFER_SIZE)) {
			perror("attach listener-capable fingerprint transport");
			goto out;
		}
		protocol.focal.exchange = qsee_focal_exchange;
		protocol.focal.context = &qsee_transport;
		puts("Listener-capable fingerprint transport attached.");
	}
	if (verify_private_directory(FP6_ACTIVE_GROUP_PATH)) {
		perror("prepare fingerprint template directory");
		goto out;
	}
	int32_t active_group_status = 0;
	int active_group_result = focal_protocol_set_active_group_status(
		&protocol.focal, group_id, FP6_ACTIVE_GROUP_PATH,
		&active_group_status);
	if (active_group_result ||
	    (active_group_status != 0 && active_group_status != -2)) {
		fputs("Fingerprint active-group selection failed.\n", stderr);
		goto out;
	}
	/*
	 * QREL 16.95.0 deliberately continues from the observed fresh-store -2
	 * result into a mandatory statistics sync.  Do not generalize that status:
	 * every other transport/trustlet result remains fatal.
	 */
	if (focal_protocol_sync_reset_statistics(&protocol.focal)) {
		fputs("Fingerprint statistics synchronization failed.\n", stderr);
		goto out;
	}
	int enumeration_result = enumerate_one(&protocol.focal,
					       &enrolled_template);
	if (enumeration_result < 0) {
		fputs("Initial secure template enumeration failed.\n", stderr);
		goto out;
	}
	if (enumeration_result > 0) {
		fputs("Refusing enrollment: template store is not empty.\n", stderr);
		goto out;
	}
	if (read_pin(pin, &pin_size)) {
		perror("read PIN");
		goto out;
	}
	if (linux_native) {
		uint8_t unused_android_token[FOCAL_TA_HARDWARE_AUTH_TOKEN_SIZE] = {};
		uint64_t challenge = 0;
		uint32_t template_id = group_id;

		/*
		 * Linux authorizes administrative enrollment with PAM and a root-only
		 * transport, rather than manufacturing an Android Gatekeeper HAT.  The
		 * stock Focal HAL exposes this exact mode as trusted_enrollment == 0.
		 * The all-zero token field is ABI padding in that mode, not a claimed
		 * authentication token.  The match-on-chip template remains in the TA.
		 */
		if (authenticate_luma_pin(pin, pin_size)) {
			fputs("Luma PIN authentication failed.\n", stderr);
			goto out;
		}
		secure_clear(pin, sizeof(pin));
		if (focal_protocol_pre_enroll(&protocol.focal, &challenge)) {
			fputs("Fingerprint pre-enrollment failed.\n", stderr);
			goto out;
		}
		secure_clear(&challenge, sizeof(challenge));
		if (focal_protocol_enroll(&protocol.focal,
					  unused_android_token, &template_id, 0)) {
			fputs("Linux-authorized fingerprint enrollment was rejected.\n",
			      stderr);
			(void)focal_protocol_post_enroll(&protocol.focal);
			goto out;
		}
		secure_clear(unused_android_token,
			     sizeof(unused_android_token));
		begin.template_id = template_id;
		enrollment_started = true;
		goto capture;
	}
	/*
	 * Match gatekeeperd's first-enrollment cold-boot contract.  Android sends
	 * DELETE_ALL_USERS once after a userdata reset, before it creates any new
	 * credential.  Luma replaced Android userdata without ever running that
	 * lifecycle, so stale secure records can otherwise exhaust the TA's fixed
	 * per-user table and make a fresh enrollment fail with -30.
	 */
	if (!post_reset) {
		size_t clear_all_response_size = 0;
		int32_t clear_all_secure_status = 0;

		if (gatekeeper_exchange(&transport,
					FP6_GATEKEEPER_DELETE_ALL_USERS,
					NULL, 0, NULL, 0,
					&clear_all_response_size,
					&clear_all_secure_status) ||
		    (clear_all_secure_status != 0 &&
		     clear_all_secure_status != -1) ||
		    clear_all_response_size) {
			fprintf(stderr,
				"Gatekeeper cold-boot reset failed: command=0x%08x "
				"secure_status=%d response_size=%zu errno=%d.\n",
				transport.last_command,
				transport.last_secure_status,
				transport.last_response_size,
				transport.last_errno);
			goto out;
		}
		/*
		 * Stock gatekeeperd deliberately ignores deleteAllUsers()'s return
		 * value. Accept only its exact observed nonfatal status.
		 */
		if (clear_all_secure_status == -1)
			puts("Gatekeeper reset completed with the stock nonfatal persistence status.");
	} else {
		/*
		 * Exact global-reset success retires keymaster64 for the rest of that
		 * boot. This mode is intentionally explicit: use it only on the fresh
		 * boot immediately following an independently gated successful reset.
		 */
		puts("Using previously accepted cold-boot Gatekeeper reset state.");
	}
	if (!post_user_reset) {
		/* CBOR map { 3: 0 }, matching QTI serializeClientDeleteUser. */
		static const uint8_t clear_user_request[] = {
			0xa1, 0x03, 0x00,
		};
		size_t clear_response_size = 0;
		int32_t clear_secure_status = 0;

		if (gatekeeper_exchange(&transport, FP6_GATEKEEPER_DELETE_USER,
					clear_user_request,
					sizeof(clear_user_request), NULL, 0,
					&clear_response_size,
					&clear_secure_status) ||
		    (clear_secure_status != 0 && clear_secure_status != -1) ||
		    clear_response_size) {
			fprintf(stderr,
				"Gatekeeper target-user reset failed: command=0x%08x "
				"secure_status=%d response_size=%zu errno=%d.\n",
				transport.last_command,
				transport.last_secure_status,
				transport.last_response_size,
				transport.last_errno);
			goto out;
		}
	} else {
		puts("Using previously accepted secure-user reset state.");
	}
	/* The stock TA returns -1 when the target secure user did not exist. */
	/* QREL 16.95.0 Gatekeeper serializes the raw credential bytes here. */
	int begin_status = fp6_enrollment_begin(&protocol, user_id, group_id, 1,
					pin, pin_size, password_handle,
					sizeof(password_handle), &begin);
	if (begin_status) {
		fprintf(stderr,
			"Authenticated enrollment setup failed: stage=%d "
			"command=0x%08x secure_status=%d response_size=%zu errno=%d.\n",
			begin_status, transport.last_command,
			transport.last_secure_status,
			transport.last_response_size, transport.last_errno);
		goto out;
	}
	secure_clear(pin, sizeof(pin));
	enrollment_started = true;

capture:
	/* QREL 16.95.0 selects work mode 1 before its enrollment IRQ loop. */
	if (focal_protocol_configure_work_mode(&protocol.focal, 1)) {
		fputs("Fingerprint enrollment work-mode setup failed.\n", stderr);
		goto out;
	}
	enrollment_work_mode = true;
	puts("Enrollment ready. Repeatedly touch and lift your finger on the power-button sensor.");
	fflush(stdout);

	unsigned int capture_cycles = 0;
	bool quota_complete = false;
	while (capture_cycles < FP6_MAX_EVENT_CYCLES && !interrupted) {
		uint32_t remaining = UINT32_MAX;
		int32_t event_status = 0;
		int result = wait_for_interrupt(focal_fd);

		if (result == 0) {
			puts("Still waiting for a sensor touch...");
			fflush(stdout);
			continue;
		}
		if (result < 0) {
			if (errno == EAGAIN)
				continue;
			perror("fingerprint interrupt");
			goto out;
		}
		if (focal_protocol_query_event_status(&protocol.focal, 0,
						      &event_status)) {
			fputs("Sensor event query failed.\n", stderr);
			goto out;
		}
		/* A prepared IRQ line can report one empty edge before first touch. */
		if (event_status == 0)
			continue;
		if (event_status == 6) {
			result = enrollment_report_release(&protocol.focal);
			if (result) {
				fprintf(stderr,
					"Fingerprint release event failed: %d\n",
					result);
				goto out;
			}
			if (quota_complete)
				puts("All samples retained. Touch once more to commit the secure template.");
			else
				puts("Lift registered. Touch a different part of your finger.");
			fflush(stdout);
			continue;
		}
		if (event_status != 5) {
			fprintf(stderr, "Unexpected enrollment event status: %d\n",
				event_status);
			goto out;
		}
		capture_cycles++;
		result = enrollment_capture_cycle(&protocol.focal, &driver, &state,
						  &remaining);
		if (result == 1) {
			puts("Lift and reposition your finger.");
			fflush(stdout);
			continue;
		}
		if (result == 2) {
			enrollment_completed = true;
			puts("Secure fingerprint completion accepted.");
			break;
		}
		if (result == 3) {
			puts("Fingerprint progress: 100%");
			if (linux_native) {
				/*
				 * Untrusted-enrollment mode returns the complete working
				 * template at state zero rather than the Android-only -11
				 * terminal. Stock immediately crosses the protected-template
				 * synchronization boundary here; no extra sensor sample exists.
				 */
				enrollment_completed = true;
				puts("All samples retained by the secure matcher.");
				break;
			}
			quota_complete = true;
			puts("All samples collected. Lift, then touch once more to commit.");
			fflush(stdout);
			continue;
		}
		if (result) {
			fprintf(stderr, "Enrollment capture cycle failed: %d\n",
				result);
			goto out;
		}
		/* Completion is recognized inside enrollment_capture_cycle(). */
		unsigned int progress = state.report_count * 5U;
		if (progress > 99U)
			progress = 99U;
		printf("Fingerprint progress: %u%%\n", progress);
		fflush(stdout);
	}
	if (!enrollment_completed) {
		fputs("Fingerprint enrollment did not complete.\n", stderr);
		goto out;
	}
	if (focal_protocol_configure_work_mode(&protocol.focal, 2)) {
		fputs("Fingerprint enrollment work-mode restore failed.\n", stderr);
		goto out;
	}
	enrollment_work_mode = false;
	if (focal_protocol_post_enroll(&protocol.focal)) {
		fputs("Fingerprint post-enrollment finalization failed.\n", stderr);
		goto out;
	}
	enrollment_started = false;
	if (linux_native) {
		/*
		 * Stock makes the completed template synchronizable only after
		 * POST_ENROLL closes the acquisition transaction. Requesting it while
		 * that transaction is still open returns the trustlet's exact ENOENT.
		 */
		protected_template = calloc(
			1, FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE);
		if (!protected_template) {
			perror("allocate protected template buffer");
			goto out;
		}
		protected_template_size = FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE;
		if (focal_protocol_export_protected_template(
			    &protocol.focal, group_id, begin.template_id,
			    protected_template, &protected_template_size)) {
			fputs("Protected fingerprint template export failed.\n", stderr);
			goto out;
		}
		if (persist_protected_template(group_id, begin.template_id,
					       protected_template,
					       protected_template_size)) {
			perror("persist protected fingerprint template");
			goto out;
		}
		if (focal_protocol_import_protected_template(
			    &protocol.focal, group_id, begin.template_id,
			    protected_template, protected_template_size)) {
			fputs("Protected fingerprint template activation failed.\n",
			      stderr);
			goto out;
		}
	}
	{
		int enumerate_result = enumerate_one(&protocol.focal,
						     &enrolled_template);

		if (enumerate_result < 0) {
			fputs("Post-enrollment enumeration failed.\n", stderr);
			goto out;
		}
		if (enumerate_result == 0) {
			fputs("Secure enrollment completed without a committed template.\n",
			      stderr);
			goto out;
		}
	}
	if (!linux_native) {
		if (persist_credential(user_id, group_id,
				       enrolled_template.finger_id, lskf_salt,
				       password_handle,
				       begin.password_handle_size)) {
			perror("persist credential handle");
			goto out;
		}
	}
	printf("Fingerprint enrolled successfully (template %u).\n",
	       enrolled_template.finger_id);
	exit_status = EXIT_SUCCESS;
out:
	if (enrollment_work_mode)
		(void)focal_protocol_configure_work_mode(&protocol.focal, 2);
	if (enrollment_started) {
		(void)focal_protocol_cancel(&protocol.focal);
		(void)focal_protocol_post_enroll(&protocol.focal);
	}
	secure_clear(pin, sizeof(pin));
	secure_clear(lskf_salt, sizeof(lskf_salt));
	secure_clear(password_handle, sizeof(password_handle));
	secure_clear(&begin, sizeof(begin));
	secure_clear(&state, sizeof(state));
	if (protected_template) {
		secure_clear(protected_template,
			     FOCAL_TA_PROTECTED_TEMPLATE_MAX_SIZE);
		free(protected_template);
	}
	if (focal_fd >= 0)
		(void)close(focal_fd);
	if (transport.secure_fd >= 0)
		(void)close(transport.secure_fd);
	if (qsee_transport.handle &&
	    QSEECom_shutdown_app(&qsee_transport.handle)) {
		perror("detach listener-capable fingerprint transport");
		exit_status = EXIT_FAILURE;
	}
	return exit_status;
}
