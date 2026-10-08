// SPDX-License-Identifier: GPL-2.0-only
/*
 * Bounded FP6 QSEEComCompat kernel-privilege control and initialization probe.
 *
 * Fairphone's downstream QSEECom compatibility layer registers a kernel-only
 * privileged QTEE client before opening service UID 122. Linux deliberately
 * prevents userspace from supplying the NULL credential object that denotes
 * that client. Diagnostic modes mirror the acquisition/load boundary for
 * Qualcomm-signed applications. The most advanced mode reproduces only the
 * audited stock FocalTech initialization sequence through template-count
 * enumeration; it contains no capture, enrollment, authentication,
 * template-write, or raw-image operation.
 */

#include <linux/capability.h>
#include <linux/device.h>
#include <linux/elf.h>
#include <linux/firmware.h>
#include <linux/mutex.h>
#include <linux/moduleparam.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/timekeeping.h>
#include <linux/vmalloc.h>

#include "qcomtee.h"
#include "luma-focal-config.h"

#define LUMA_CONTROL_APP "smplap64"
#define LUMA_FINGERPRINT_APP "focal64"
#define LUMA_KEYMASTER_APP "keymaster"
#define LUMA_KEYMASTER_PARTITION_APP "keymaster64"
#define LUMA_KEYMASTER_PARTITION_FW "keymaster64.elf"
#define LUMA_KEYMASTER_PARTITION_SIZE 441312U
#define LUMA_CONTROL_STANDARD_APP_LOADER_UID 3U
#define LUMA_CONTROL_STANDARD_LOAD_FROM_BUFFER 0U
#define LUMA_CONTROL_STANDARD_UNLOAD 1U
#define LUMA_CONTROL_APP_LOADER_UID 122U
#define LUMA_CONTROL_LOAD_FROM_BUFFER 1U
#define LUMA_CONTROL_LOOKUP_TA 2U
#define LUMA_CONTROL_UNLOAD 2U
#define LUMA_CONTROL_NOT_LOADED 23
#define LUMA_CONTROL_SPLITS 9U
#define LUMA_CONTROL_MAX_ELF SZ_4M
#define LUMA_CONTROL_DIST_NAME_SIZE 256U
#define LUMA_FOCAL_SEND_SIZE 0x78000U
#define LUMA_FOCAL_RESPONSE_SIZE 0x8040U
#define LUMA_FOCAL_RESPONSE_BIT 0x80000000U
#define LUMA_FOCAL_SYNC_ENCAPSULATED_KEY 0x1011U
#define LUMA_FOCAL_INITIALIZE_SPI 0x1006U
#define LUMA_FOCAL_FREE_SPI 0x1007U
#define LUMA_FOCAL_PROBE_DEVICE 0x100aU
#define LUMA_FOCAL_INITIALIZE_DEVICE 0x100bU
#define LUMA_FOCAL_SYNC_CONFIG 0x100dU
#define LUMA_FOCAL_INITIALIZE 0x1004U
#define LUMA_FOCAL_ENUMERATE 0x2005U
#define LUMA_FOCAL_INITIALIZATION_BYTES 192U
#define LUMA_FOCAL_DEVICE_DATA_BYTES 60U
#define LUMA_FOCAL_MAX_TEMPLATES 32U
#define LUMA_FOCAL_PROBE_RETRY_STATUS (-205)
#define LUMA_QSEECOMCOMPAT_SEND_REQUEST 0U
#define LUMA_KEYMASTER_SERVICE_UID 151U
#define LUMA_KEYMASTER_SELECTOR 150U
#define LUMA_KEYMASTER_INVOKE 0U
#define LUMA_KEYMASTER_GET_VERSION 0x200U
#define LUMA_KEYMASTER_SET_CLIENT_VERSION 0x207U
#define LUMA_KEYMASTER_SET_KEYMINT_VERSION 0x3121U
#define LUMA_KEYMASTER_CONFIGURE 0x2116U
#define LUMA_KEYMASTER_GET_HMAC_SHARING_PARAMS 0x220eU
#define LUMA_KEYMASTER_COMPUTE_SHARING_HMAC 0x220fU
#define LUMA_KEYMASTER_GET_ENCAPSULATED_KEY 0x205U
#define LUMA_KEYMASTER_UNSUPPORTED_OPERATION (-38)
#define LUMA_KEYMASTER_REQUEST_SIZE 0x40U
#define LUMA_KEYMASTER_RESPONSE_SIZE 0x800U
#define LUMA_KEYMASTER_SHARED_BUFFER_SIZE 0xa000U
#define LUMA_KEYMASTER_HMAC_PARAMS_SIZE 73U
#define LUMA_KEYMASTER_HMAC_RESULT_SIZE 37U
#define LUMA_KEYMASTER_HMAC_ENTRY_SIZE 72U
#define LUMA_KEYMASTER_HMAC_CLASSIC_ENTRY_SIZE 64U
#define LUMA_KEYMASTER_OS_VERSION 160000U
#define LUMA_KEYMASTER_OS_PATCHLEVEL 202607U
#define LUMA_KEYMASTER_VENDOR_PATCHLEVEL 202607U
#define LUMA_KEYMASTER_TAG_OS_VERSION 0x300002c1U
#define LUMA_KEYMASTER_TAG_OS_PATCHLEVEL 0x300002c2U
#define LUMA_KEYMASTER_TAG_VENDOR_PATCHLEVEL 0x300002ceU
#define LUMA_KEYMASTER_ANDROID_UID 1000U
#define LUMA_ANDROID_CLIENT_ENV_REGISTER 2U
#define LUMA_CREDENTIAL_GET_LENGTH 0U
#define LUMA_CREDENTIAL_READ_AT_OFFSET 1U

static bool luma_control_identity;
module_param_named(luma_control_identity, luma_control_identity, bool, 0400);
MODULE_PARM_DESC(luma_control_identity,
		 "run the bounded non-biometric smplap64 identity control once");

static bool luma_standard_identity;
module_param_named(luma_standard_identity, luma_standard_identity, bool, 0400);
MODULE_PARM_DESC(luma_standard_identity,
		 "run the bounded standard AppLoader smplap64 identity control once");

static bool luma_focal_identity;
module_param_named(luma_focal_identity, luma_focal_identity, bool, 0400);
MODULE_PARM_DESC(luma_focal_identity,
		 "run the bounded QSEEComCompat focal64 identity control once");

static bool luma_focal_enumerate;
module_param_named(luma_focal_enumerate, luma_focal_enumerate, bool, 0400);
MODULE_PARM_DESC(luma_focal_enumerate,
		 "initialize focal64 and enumerate template metadata without capture");

static bool luma_keymaster_lookup;
module_param_named(luma_keymaster_lookup, luma_keymaster_lookup, bool, 0400);
MODULE_PARM_DESC(luma_keymaster_lookup,
		 "look up the stock keymaster QSEE application without invoking it");

struct luma_focal_message {
	u32 command;
	u32 payload_length;
	s32 status;
	u32 elapsed_or_flags;
	u8 payload[];
};

struct luma_keymaster_response {
	s32 status;
	u32 blob_offset;
	u32 blob_length;
};

struct luma_keymaster_generic_response {
	s32 status;
	u32 payload_length;
	u8 payload[];
};

struct luma_keymaster_version_response {
	s32 status;
	u32 qseecom_version;
	u32 keymaster_api_version;
	u32 keymaster_version;
	u32 keymaster_minor_version;
};

struct luma_android_credential {
	struct qcomtee_object object;
	u8 cbor[32];
	size_t size;
};

struct luma_gatekeeper_session {
	struct qcomtee_object *client_env;
	struct qcomtee_object *service;
	struct qcomtee_object *app_client;
	struct qcomtee_object *keymint_controller;
	struct qcomtee_object *gatekeeper_client_env;
	struct qcomtee_object *gatekeeper_service;
	struct qcomtee_object *gatekeeper_app_client;
	struct qcomtee_object *controller;
};

enum luma_focal_stage {
	LUMA_FOCAL_STAGE_IDLE,
	LUMA_FOCAL_STAGE_SECURE_PREPARED,
	LUMA_FOCAL_STAGE_PROBE_RETRY,
	LUMA_FOCAL_STAGE_DEVICE_INITIALIZED,
	LUMA_FOCAL_STAGE_COMPLETE,
	LUMA_FOCAL_STAGE_FAILED,
};

struct luma_focal_session {
	struct qcomtee *qcomtee;
	struct device *dev;
	struct qcomtee_object *client_env;
	struct qcomtee_object *loader;
	struct qcomtee_object *controller;
	struct luma_gatekeeper_session gatekeeper;
	enum luma_focal_stage stage;
	u8 probe_retries;
	bool spi_initialized;
	bool attribute_created;
	u8 *capture_send_buffer;
	u8 *capture_response_buffer;
	struct qcomtee_object_invoke_ctx *capture_oic;
	bool capture_buffer_pending;
};

static DEFINE_MUTEX(luma_focal_session_lock);
static struct luma_focal_session luma_focal_session;

/*
 * focal64 retains opaque acquisition state in its QSEECom shared buffer from
 * CAPTURE_IMAGE through the immediately following REPORT_EVENT. Keep that
 * state kernel-private for exactly that pair of calls, then wipe it. Nothing
 * in these buffers is ever copied to userspace except the bounded protocol
 * response selected by luma_focal_exchange_status().
 */
static void luma_focal_capture_buffers_clear(
	struct luma_focal_session *session)
{
	if (session->capture_oic) {
		qcomtee_msg_buffers_free(session->capture_oic);
		kfree(session->capture_oic);
	}
	if (session->capture_response_buffer) {
		memzero_explicit(session->capture_response_buffer,
				 LUMA_FOCAL_RESPONSE_SIZE);
		vfree(session->capture_response_buffer);
	}
	if (session->capture_send_buffer) {
		memzero_explicit(session->capture_send_buffer,
				 LUMA_FOCAL_SEND_SIZE);
		vfree(session->capture_send_buffer);
	}
	session->capture_response_buffer = NULL;
	session->capture_send_buffer = NULL;
	session->capture_oic = NULL;
	session->capture_buffer_pending = false;
}

static void luma_control_put_sync(struct qcomtee *qcomtee,
				  struct qcomtee_object **object)
{
	if (*object == NULL_QCOMTEE_OBJECT)
		return;
	qcomtee_object_put(*object);
	*object = NULL_QCOMTEE_OBJECT;
	/*
	 * Remote-object releases run on qcomtee's ordered workqueue.  A parent
	 * and child must not be queued together: secure world can release the
	 * child while Linux is still processing the parent's async release.
	 */
	flush_workqueue(qcomtee->wq);
}

static void luma_gatekeeper_release(
	struct qcomtee *qcomtee, struct luma_gatekeeper_session *session)
{
	luma_control_put_sync(qcomtee, &session->controller);
	luma_control_put_sync(qcomtee, &session->gatekeeper_app_client);
	luma_control_put_sync(qcomtee, &session->gatekeeper_service);
	luma_control_put_sync(qcomtee, &session->gatekeeper_client_env);
	luma_control_put_sync(qcomtee, &session->keymint_controller);
	luma_control_put_sync(qcomtee, &session->app_client);
	luma_control_put_sync(qcomtee, &session->service);
	luma_control_put_sync(qcomtee, &session->client_env);
}

static int luma_control_invoke(struct qcomtee *qcomtee,
			       struct qcomtee_object *object, u32 op,
			       struct qcomtee_arg *args, int *result)
{
	struct qcomtee_object_invoke_ctx *oic;
	int ret;

	oic = qcomtee_object_invoke_ctx_alloc(qcomtee->ctx);
	if (!oic)
		return -ENOMEM;
	ret = qcomtee_object_do_invoke(oic, object, op, args, result);
	kfree(oic);
	return ret;
}

#include "qcomtee-listener-extension.inc"

static size_t luma_cbor_uint(u8 *output, u64 value)
{
	int i;

	if (value < 24) {
		output[0] = value;
		return 1;
	}
	if (value <= U8_MAX) {
		output[0] = 0x18;
		output[1] = value;
		return 2;
	}
	if (value <= U16_MAX) {
		output[0] = 0x19;
		output[1] = value >> 8;
		output[2] = value;
		return 3;
	}
	if (value <= U32_MAX) {
		output[0] = 0x1a;
		output[1] = value >> 24;
		output[2] = value >> 16;
		output[3] = value >> 8;
		output[4] = value;
		return 5;
	}
	output[0] = 0x1b;
	for (i = 0; i < 8; i++)
		output[i + 1] = value >> (56 - i * 8);
	return 9;
}

static int luma_android_credential_dispatch(
				struct qcomtee_object_invoke_ctx *oic,
				struct qcomtee_object *object, u32 op,
				struct qcomtee_arg *args)
{
	struct luma_android_credential *credential =
		container_of(object, struct luma_android_credential, object);
	u64 offset;
	size_t available;
	int count = qcomtee_args_len(args);

	pr_info("luma-keymaster: credential callback op=%u args=%d type0=%u size0=%zu type1=%u size1=%zu contents_logged=0\n",
		op, count, count > 0 ? args[0].type : 0,
		count > 0 && args[0].type != QCOMTEE_ARG_TYPE_IO &&
		args[0].type != QCOMTEE_ARG_TYPE_OO ? args[0].b.size : 0,
		count > 1 ? args[1].type : 0,
		count > 1 && args[1].type != QCOMTEE_ARG_TYPE_IO &&
		args[1].type != QCOMTEE_ARG_TYPE_OO ? args[1].b.size : 0);

	if (op == LUMA_CREDENTIAL_GET_LENGTH) {
		if (count != 1 ||
		    args[0].type != QCOMTEE_ARG_TYPE_OB ||
		    args[0].b.size < sizeof(credential->size))
			return -EINVAL;
		memcpy(args[0].b.addr, &credential->size,
		       sizeof(credential->size));
		args[0].b.size = sizeof(credential->size);
		return 0;
	}

	if (op == LUMA_CREDENTIAL_READ_AT_OFFSET) {
		if (count != 2 ||
		    args[0].type != QCOMTEE_ARG_TYPE_IB ||
		    args[1].type != QCOMTEE_ARG_TYPE_OB ||
		    args[0].b.size != sizeof(offset))
			return -EINVAL;
		memcpy(&offset, args[0].b.addr, sizeof(offset));
		if (offset >= credential->size)
			return -EINVAL;
		available = credential->size - offset;
		args[1].b.size = min(args[1].b.size, available);
		memcpy(args[1].b.addr, credential->cbor + offset,
		       args[1].b.size);
		return 0;
	}

	return -EINVAL;
}

static void luma_android_credential_release(struct qcomtee_object *object)
{
	struct luma_android_credential *credential =
		container_of(object, struct luma_android_credential, object);

	memzero_explicit(credential->cbor, sizeof(credential->cbor));
	kfree(credential);
}

static struct qcomtee_object_operations luma_android_credential_ops = {
	.release = luma_android_credential_release,
	.dispatch = luma_android_credential_dispatch,
};

static struct qcomtee_object *
luma_keymaster_get_client_env(struct qcomtee *qcomtee)
{
	struct luma_android_credential *credential;
	struct qcomtee_object_invoke_ctx *oic;
	struct qcomtee_arg args[3] = { 0 };
	struct timespec64 now;
	u64 milliseconds;
	size_t cursor = 0;
	int ret, result = QCOMTEE_MSG_ERROR_UNAVAIL;

	credential = kzalloc_obj(*credential, GFP_KERNEL);
	if (!credential)
		return NULL_QCOMTEE_OBJECT;
	ret = qcomtee_object_user_init(&credential->object,
				       QCOMTEE_OBJECT_TYPE_CB,
				       &luma_android_credential_ops,
				       "luma-keymaster-credential");
	if (ret) {
		kfree(credential);
		return NULL_QCOMTEE_OBJECT;
	}

	ktime_get_real_ts64(&now);
	milliseconds = (u64)now.tv_sec * MSEC_PER_SEC +
		(u64)now.tv_nsec / NSEC_PER_MSEC;
	credential->cbor[cursor++] = 0xa2; /* map(2) */
	cursor += luma_cbor_uint(credential->cbor + cursor, 1);
	cursor += luma_cbor_uint(credential->cbor + cursor,
				LUMA_KEYMASTER_ANDROID_UID);
	cursor += luma_cbor_uint(credential->cbor + cursor, 6);
	cursor += luma_cbor_uint(credential->cbor + cursor, milliseconds);
	credential->size = cursor;

	args[0].o = &credential->object;
	args[0].type = QCOMTEE_ARG_TYPE_IO;
	args[1].type = QCOMTEE_ARG_TYPE_OO;
	oic = qcomtee_object_invoke_ctx_alloc(qcomtee->ctx);
	if (!oic) {
		qcomtee_object_put(&credential->object);
		return NULL_QCOMTEE_OBJECT;
	}
	ret = qcomtee_object_do_invoke(oic, ROOT_QCOMTEE_OBJECT,
				       LUMA_ANDROID_CLIENT_ENV_REGISTER,
				       args, &result);
	kfree(oic);
	/* The invoke consumes the callback-object input on every return path. */
	if (ret || result) {
		pr_err("luma-keymaster: Android credential registration transport=%d result=%d\n",
		       ret, result);
		return NULL_QCOMTEE_OBJECT;
	}
	pr_info("luma-keymaster: Android KeyMint credential accepted uid=%u contents_logged=0\n",
		LUMA_KEYMASTER_ANDROID_UID);
	return args[1].o;
}

static int luma_focal_exchange_status(struct qcomtee *qcomtee,
				      struct qcomtee_object *controller,
				      u32 command, const void *payload,
				      size_t payload_size,
				      void *response_payload,
				      size_t response_capacity,
				      size_t *response_size,
				      s32 *trustlet_status,
				      u8 *retained_send_buffer,
				      u8 *retained_response_buffer,
				      struct qcomtee_object_invoke_ctx *retained_oic)
{
	struct qcomtee_arg args[11] = { 0 };
	struct luma_focal_message *request;
	struct luma_focal_message *response;
	u8 *send_buffer = NULL;
	u8 *response_buffer = NULL;
	bool retained_buffers;
	u32 is_64 = 1;
	int ret, result = QCOMTEE_MSG_ERROR_UNAVAIL;

	if (payload_size > LUMA_FOCAL_SEND_SIZE - sizeof(*request) ||
	    response_capacity > LUMA_FOCAL_RESPONSE_SIZE - sizeof(*response))
		return -E2BIG;
	if (trustlet_status)
		*trustlet_status = 0;
	retained_buffers = retained_send_buffer && retained_response_buffer;
	if (!!retained_send_buffer != !!retained_response_buffer)
		return -EINVAL;

	send_buffer = retained_send_buffer ?: vzalloc(LUMA_FOCAL_SEND_SIZE);
	response_buffer = retained_response_buffer ?:
		vzalloc(LUMA_FOCAL_RESPONSE_SIZE);
	if (!send_buffer || !response_buffer) {
		ret = -ENOMEM;
		goto out;
	}
	request = (void *)send_buffer;
	/*
	 * QSEEComCompat mirrors the modified request buffer through reqOut
	 * (argument 4).  Fairphone's focal64 writes its protocol response there;
	 * rspOut (argument 5) is the trustlet log buffer, not the command reply.
	 */
	response = (void *)send_buffer;
	request->command = command;
	request->payload_length = payload_size;
	if (payload_size)
		memcpy(request->payload, payload, payload_size);

	args[0].b.addr = send_buffer;
	args[0].b.size = LUMA_FOCAL_SEND_SIZE;
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].b.addr = response_buffer;
	args[1].b.size = LUMA_FOCAL_RESPONSE_SIZE;
	args[1].type = QCOMTEE_ARG_TYPE_IB;
	args[2].b.addr = NULL;
	args[2].b.size = 0;
	args[2].type = QCOMTEE_ARG_TYPE_IB;
	args[3].b.addr = &is_64;
	args[3].b.size = sizeof(is_64);
	args[3].type = QCOMTEE_ARG_TYPE_IB;
	args[4].b.addr = send_buffer;
	args[4].b.size = LUMA_FOCAL_SEND_SIZE;
	args[4].type = QCOMTEE_ARG_TYPE_OB;
	args[5].b.addr = response_buffer;
	args[5].b.size = LUMA_FOCAL_RESPONSE_SIZE;
	args[5].type = QCOMTEE_ARG_TYPE_OB;
	args[6].o = NULL_QCOMTEE_OBJECT;
	args[6].type = QCOMTEE_ARG_TYPE_IO;
	args[7].o = NULL_QCOMTEE_OBJECT;
	args[7].type = QCOMTEE_ARG_TYPE_IO;
	args[8].o = NULL_QCOMTEE_OBJECT;
	args[8].type = QCOMTEE_ARG_TYPE_IO;
	args[9].o = NULL_QCOMTEE_OBJECT;
	args[9].type = QCOMTEE_ARG_TYPE_IO;

	if (retained_oic)
		ret = qcomtee_object_do_invoke(
			retained_oic, controller,
			LUMA_QSEECOMCOMPAT_SEND_REQUEST, args, &result);
	else
		ret = luma_control_invoke(qcomtee, controller,
					  LUMA_QSEECOMCOMPAT_SEND_REQUEST,
					  args, &result);
	if (ret || result) {
		pr_err("luma-focal: command=0x%x transport=%d result=%d\n",
		       command, ret, result);
		ret = ret ?: -EREMOTEIO;
		goto out;
	}
	if (args[4].b.size < sizeof(*response) ||
	    response->payload_length > args[4].b.size - sizeof(*response) ||
	    response->command != (command | LUMA_FOCAL_RESPONSE_BIT)) {
		pr_err("luma-focal: command=0x%x malformed response command=0x%x payload=%u bytes=%zu\n",
		       command, response->command, response->payload_length,
		       args[4].b.size);
		ret = -EPROTO;
		goto out;
	}
	if (response->status) {
		if (trustlet_status)
			*trustlet_status = response->status;
		pr_err("luma-focal: command=0x%x trustlet_status=%d\n",
		       command, response->status);
		ret = -EREMOTEIO;
		goto out;
	}
	/*
	 * Commands without a response body leave the in/out payload-size word
	 * unchanged.  The response command and status above are authoritative;
	 * do not mistake the original request length for returned data.
	 */
	if (!response_capacity) {
		if ((command == LUMA_FOCAL_SYNC_ENCAPSULATED_KEY &&
		     response->payload_length != payload_size) ||
		    (command != LUMA_FOCAL_SYNC_ENCAPSULATED_KEY &&
		     response->payload_length != 0)) {
			ret = -EPROTO;
			goto out;
		}
		*response_size = 0;
		pr_info("luma-focal: command=0x%x accepted response_payload=0\n",
			command);
		ret = 0;
		goto out;
	}
	if (response->payload_length > response_capacity) {
		ret = -ENOSPC;
		goto out;
	}
	if (response->payload_length)
		memcpy(response_payload, response->payload,
		       response->payload_length);
	*response_size = response->payload_length;
	pr_info("luma-focal: command=0x%x accepted response_payload=%zu\n",
		command, *response_size);
	ret = 0;

out:
	if (response_buffer && !retained_buffers) {
		memzero_explicit(response_buffer, LUMA_FOCAL_RESPONSE_SIZE);
		vfree(response_buffer);
	}
	if (send_buffer && !retained_buffers) {
		memzero_explicit(send_buffer, LUMA_FOCAL_SEND_SIZE);
		vfree(send_buffer);
	}
	return ret;
}

static int luma_focal_exchange(struct qcomtee *qcomtee,
			       struct qcomtee_object *controller, u32 command,
			       const void *payload, size_t payload_size,
			       void *response_payload, size_t response_capacity,
			       size_t *response_size)
{
	return luma_focal_exchange_status(qcomtee, controller, command,
					  payload, payload_size,
					  response_payload,
					  response_capacity,
					  response_size, NULL, NULL, NULL, NULL);
}

static int luma_keymaster_generic_exchange(
				struct qcomtee *qcomtee,
				struct qcomtee_object *controller,
				u32 command, const void *payload,
				size_t payload_size, void *response_payload,
				size_t response_capacity, size_t *response_size,
				s32 *trustlet_status)
{
	/* The final zero entry is QCOMTEE_ARG_TYPE_INV and terminates the list. */
	struct qcomtee_arg args[3] = { 0 };
	struct luma_keymaster_generic_response *header;
	u8 *request = NULL;
	u8 *response = NULL;
	size_t request_size, response_buffer_size;
	int ret, result = QCOMTEE_MSG_ERROR_UNAVAIL;

	if (!response_size || !trustlet_status ||
	    payload_size > LUMA_KEYMASTER_SHARED_BUFFER_SIZE - sizeof(command))
		return -EINVAL;
	*response_size = 0;
	*trustlet_status = 0;
	request_size = sizeof(command) + payload_size;
	response_buffer_size = LUMA_KEYMASTER_SHARED_BUFFER_SIZE -
			       ALIGN(request_size, sizeof(u32));
	if (response_capacity > response_buffer_size - sizeof(*header))
		return -EINVAL;
	request = kzalloc(request_size, GFP_KERNEL);
	response = kzalloc(response_buffer_size, GFP_KERNEL);
	if (!request || !response) {
		ret = -ENOMEM;
		goto out;
	}
	memcpy(request, &command, sizeof(command));
	if (payload_size)
		memcpy(request + sizeof(command), payload, payload_size);
	args[0].b.addr = request;
	args[0].b.size = request_size;
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].b.addr = response;
	args[1].b.size = response_buffer_size;
	args[1].type = QCOMTEE_ARG_TYPE_OB;
	ret = luma_control_invoke(qcomtee, controller,
				  LUMA_KEYMASTER_INVOKE, args, &result);
	if (ret || result) {
		pr_err("luma-keymaster: command=0x%x transport=%d result=%d\n",
		       command, ret, result);
		ret = ret ?: -EREMOTEIO;
		goto out;
	}
	if (args[1].b.size < sizeof(*header)) {
		ret = -EPROTO;
		goto out;
	}
	header = (void *)response;
	*trustlet_status = header->status;
	if (header->status) {
		pr_info("luma-keymaster: command=0x%x trustlet_status=%d\n",
			command, header->status);
		ret = 0;
		goto out;
	}
	/*
	 * The second response word is an in/out blob capacity.  Commands with
	 * no response payload (notably SET_KEYMINT_VERSION) leave that capacity
	 * unchanged; only serializers that emit CBOR replace it with a length.
	 */
	if (!response_capacity) {
		*response_size = 0;
		ret = 0;
		goto out;
	}
	if (header->payload_length > args[1].b.size - sizeof(*header) ||
	    header->payload_length > response_capacity) {
		pr_err("luma-keymaster: command=0x%x malformed payload=%u bytes=%zu capacity=%zu\n",
		       command, header->payload_length, args[1].b.size,
		       response_capacity);
		ret = -EPROTO;
		goto out;
	}
	if (header->payload_length)
		memcpy(response_payload, header->payload,
		       header->payload_length);
	*response_size = header->payload_length;
	ret = 0;

out:
	if (response) {
		memzero_explicit(response, response_buffer_size);
		kfree(response);
	}
	if (request) {
		memzero_explicit(request, request_size);
		kfree(request);
	}
	return ret;
}

static int luma_keymaster_configure(
				struct qcomtee *qcomtee,
				struct qcomtee_object *controller)
{
	u8 payload[64] = { 0 };
	size_t cursor = 0, response_size = 0;
	s32 trustlet_status = 0;
	int ret;

	/*
	 * Stock libqtikeymint serializes the non-SPU configure request as a CBOR
	 * map containing the parameter count followed by the three unsigned
	 * KeyMint tags below.  These values are taken from the exact QREL 16.95.0
	 * factory image rather than from the Fedora userspace.
	 */
	payload[cursor++] = 0xa4; /* map(4) */
	cursor += luma_cbor_uint(payload + cursor, 22);
	cursor += luma_cbor_uint(payload + cursor, 3);
	cursor += luma_cbor_uint(payload + cursor,
				LUMA_KEYMASTER_TAG_OS_VERSION);
	cursor += luma_cbor_uint(payload + cursor,
				LUMA_KEYMASTER_OS_VERSION);
	cursor += luma_cbor_uint(payload + cursor,
				LUMA_KEYMASTER_TAG_OS_PATCHLEVEL);
	cursor += luma_cbor_uint(payload + cursor,
				LUMA_KEYMASTER_OS_PATCHLEVEL);
	cursor += luma_cbor_uint(payload + cursor,
				LUMA_KEYMASTER_TAG_VENDOR_PATCHLEVEL);
	cursor += luma_cbor_uint(payload + cursor,
				LUMA_KEYMASTER_VENDOR_PATCHLEVEL);

	ret = luma_keymaster_generic_exchange(
		qcomtee, controller, LUMA_KEYMASTER_CONFIGURE,
		payload, cursor, NULL, 0, &response_size, &trustlet_status);
	if (ret)
		goto out;
	if (trustlet_status) {
		pr_err("luma-keymaster: configure rejected status=%d\n",
		       trustlet_status);
		ret = -EREMOTEIO;
		goto out;
	}
	if (response_size) {
		ret = -EPROTO;
		goto out;
	}
	pr_info("luma-keymaster: configure accepted os_version=%u os_patch=%u vendor_patch=%u\n",
		LUMA_KEYMASTER_OS_VERSION, LUMA_KEYMASTER_OS_PATCHLEVEL,
		LUMA_KEYMASTER_VENDOR_PATCHLEVEL);
out:
	memzero_explicit(payload, sizeof(payload));
	return ret;
}

static int luma_keymaster_establish_hmac(
				struct qcomtee *qcomtee,
				struct qcomtee_object *controller)
{
	static const u8 params_prefix[] = { 0xa2, 0x18, 0x29, 0x58, 0x20 };
	static const u8 nonce_prefix[] = { 0x18, 0x2a, 0x58, 0x20 };
	static const u8 compute_prefix[] = { 0xa1, 0x18, 0x2b, 0x58, 0x48 };
	static const u8 classic_prefix[] = { 0xa1, 0x18, 0x2b, 0x58, 0x40 };
	static const u8 result_prefix[] = { 0xa1, 0x18, 0x2c, 0x58, 0x20 };
	u8 params[LUMA_KEYMASTER_HMAC_PARAMS_SIZE] = { 0 };
	u8 compute[sizeof(compute_prefix) + LUMA_KEYMASTER_HMAC_ENTRY_SIZE] = { 0 };
	u8 classic[sizeof(classic_prefix) +
		   LUMA_KEYMASTER_HMAC_CLASSIC_ENTRY_SIZE] = { 0 };
	u8 result[LUMA_KEYMASTER_HMAC_RESULT_SIZE] = { 0 };
	__le64 seed_length = cpu_to_le64(32);
	size_t response_size = 0;
	s32 trustlet_status = 0;
	int ret;

	ret = luma_keymaster_generic_exchange(
		qcomtee, controller, LUMA_KEYMASTER_GET_HMAC_SHARING_PARAMS,
		NULL, 0, params, sizeof(params), &response_size,
		&trustlet_status);
	if (ret || trustlet_status)
		goto out_status;
	if (response_size != sizeof(params) ||
	    memcmp(params, params_prefix, sizeof(params_prefix)) ||
	    memcmp(params + sizeof(params_prefix) + 32,
		   nonce_prefix, sizeof(nonce_prefix))) {
		ret = -EPROTO;
		goto out;
	}

	memcpy(compute, compute_prefix, sizeof(compute_prefix));
	memcpy(compute + sizeof(compute_prefix),
	       params + sizeof(params_prefix), 32);
	memcpy(compute + sizeof(compute_prefix) + 32,
	       params + sizeof(params_prefix) + 32 + sizeof(nonce_prefix), 32);
	memcpy(compute + sizeof(compute_prefix) + 64,
	       &seed_length, sizeof(seed_length));
	ret = luma_keymaster_generic_exchange(
		qcomtee, controller, LUMA_KEYMASTER_COMPUTE_SHARING_HMAC,
		compute, sizeof(compute), result, sizeof(result),
		&response_size, &trustlet_status);
	if (ret)
		goto out;
	if (trustlet_status == LUMA_KEYMASTER_UNSUPPORTED_OPERATION) {
		memzero_explicit(result, sizeof(result));
		memcpy(classic, classic_prefix, sizeof(classic_prefix));
		memcpy(classic + sizeof(classic_prefix),
		       params + sizeof(params_prefix), 32);
		memcpy(classic + sizeof(classic_prefix) + 32,
		       params + sizeof(params_prefix) + 32 +
		       sizeof(nonce_prefix), 32);
		ret = luma_keymaster_generic_exchange(
			qcomtee, controller,
			LUMA_KEYMASTER_COMPUTE_SHARING_HMAC,
			classic, sizeof(classic), result, sizeof(result),
			&response_size, &trustlet_status);
		if (ret)
			goto out;
	}
	if (trustlet_status)
		goto out_status;
	if (response_size != sizeof(result) ||
	    memcmp(result, result_prefix, sizeof(result_prefix))) {
		ret = -EPROTO;
		goto out;
	}
	pr_info("luma-keymaster: shared-HMAC negotiation accepted participants=1 secret_material_logged=0\n");
	ret = 0;
	goto out;

out_status:
	if (!ret) {
		pr_err("luma-keymaster: shared-HMAC negotiation rejected status=%d\n",
		       trustlet_status);
		ret = -EREMOTEIO;
	}
out:
	memzero_explicit(result, sizeof(result));
	memzero_explicit(classic, sizeof(classic));
	memzero_explicit(compute, sizeof(compute));
	memzero_explicit(params, sizeof(params));
	return ret;
}

static int luma_keymaster_prepare_client(
				struct qcomtee *qcomtee,
				struct qcomtee_object *controller,
				bool prepare_keymint)
{
	static const u8 client_version_request[24] = {
		0x07, 0x02, 0x00, 0x00,
		0x04, 0x00, 0x00, 0x00,
		0x05, 0x00, 0x00, 0x00,
		0x04, 0x00, 0x00, 0x00,
		0x05, 0x00, 0x00, 0x00,
		0x00, 0x00, 0x00, 0x00,
	};
	static const u8 keymint_version_payload[] = {
		0xa1, 0x0b, 0x19, 0x01, 0x2c,
	};
	struct luma_keymaster_version_response *version;
	/* The final zero entry is QCOMTEE_ARG_TYPE_INV and terminates the list. */
	struct qcomtee_arg args[3] = { 0 };
	u8 get_version_request[sizeof(u32)] = { 0 };
	u8 *response = NULL;
	size_t response_capacity, response_size = 0;
	s32 status = 0;
	u32 command = LUMA_KEYMASTER_GET_VERSION;
	int ret, result = QCOMTEE_MSG_ERROR_UNAVAIL;

	response_capacity = LUMA_KEYMASTER_SHARED_BUFFER_SIZE -
			    sizeof(get_version_request);
	response = kzalloc(response_capacity, GFP_KERNEL);
	if (!response)
		return -ENOMEM;
	memcpy(get_version_request, &command, sizeof(command));
	args[0].b.addr = get_version_request;
	args[0].b.size = sizeof(get_version_request);
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].b.addr = response;
	args[1].b.size = response_capacity;
	args[1].type = QCOMTEE_ARG_TYPE_OB;
	ret = luma_control_invoke(qcomtee, controller,
				  LUMA_KEYMASTER_INVOKE, args, &result);
	if (ret || result) {
		pr_err("luma-keymaster: command=0x%x transport=%d result=%d\n",
		       command, ret, result);
		ret = ret ?: -EREMOTEIO;
		goto out;
	}
	version = (void *)response;
	if (args[1].b.size < sizeof(*version) || version->status) {
		pr_err("luma-keymaster: command=0x%x malformed_or_status=%d bytes=%zu\n",
		       command, version->status, args[1].b.size);
		ret = -EPROTO;
		goto out;
	}
	pr_info("luma-keymaster: command=0x%x accepted qseecom=%u api=%u ta=%u minor=%u\n",
		command, version->qseecom_version,
		version->keymaster_api_version, version->keymaster_version,
		version->keymaster_minor_version);

	memzero_explicit(response, response_capacity);
	memset(args, 0, sizeof(args));
	args[0].b.addr = (void *)client_version_request;
	args[0].b.size = sizeof(client_version_request);
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].b.addr = response;
	args[1].b.size = LUMA_KEYMASTER_SHARED_BUFFER_SIZE -
			 sizeof(client_version_request);
	args[1].type = QCOMTEE_ARG_TYPE_OB;
	result = QCOMTEE_MSG_ERROR_UNAVAIL;
	ret = luma_control_invoke(qcomtee, controller,
				  LUMA_KEYMASTER_INVOKE, args, &result);
	if (ret || result) {
		pr_err("luma-keymaster: command=0x%x transport=%d result=%d\n",
		       LUMA_KEYMASTER_SET_CLIENT_VERSION, ret, result);
		ret = ret ?: -EREMOTEIO;
		goto out;
	}
	memcpy(&status, response, sizeof(status));
	if (status) {
		pr_err("luma-keymaster: command=0x%x status=%d\n",
		       LUMA_KEYMASTER_SET_CLIENT_VERSION, status);
		ret = -EREMOTEIO;
		goto out;
	}
	pr_info("luma-keymaster: command=0x%x accepted client_version=5 contents_logged=0\n",
		LUMA_KEYMASTER_SET_CLIENT_VERSION);
	if (!prepare_keymint) {
		ret = 0;
		goto out;
	}

	ret = luma_keymaster_generic_exchange(
		qcomtee, controller, LUMA_KEYMASTER_SET_KEYMINT_VERSION,
		keymint_version_payload, sizeof(keymint_version_payload),
		NULL, 0, &response_size, &status);
	if (ret || status) {
		if (!ret)
			ret = -EREMOTEIO;
		goto out;
	}
	if (response_size) {
		ret = -EPROTO;
		goto out;
	}
	pr_info("luma-keymaster: command=0x%x accepted keymint_message_version=300\n",
		LUMA_KEYMASTER_SET_KEYMINT_VERSION);
	ret = luma_keymaster_configure(qcomtee, controller);
	if (ret)
		goto out;
	ret = luma_keymaster_establish_hmac(qcomtee, controller);
	if (ret)
		goto out;
	ret = 0;

out:
	memzero_explicit(response, response_capacity);
	kfree(response);
	memzero_explicit(get_version_request, sizeof(get_version_request));
	return ret;
}

static int luma_keymaster_prepare_service(
	struct qcomtee *qcomtee, struct luma_gatekeeper_session *session)
{
	struct qcomtee_object_invoke_ctx *oic;
	/* Two arguments plus the mandatory QCOMTEE_ARG_TYPE_INV terminator. */
	struct qcomtee_arg args[3] = { 0 };
	u32 selector = LUMA_KEYMASTER_SELECTOR;
	int ret = -ENODEV;
	int result = QCOMTEE_MSG_ERROR_UNAVAIL;

	memset(session, 0, sizeof(*session));
	session->client_env = luma_keymaster_get_client_env(qcomtee);
	if (session->client_env == NULL_QCOMTEE_OBJECT)
		return -EACCES;

	oic = qcomtee_object_invoke_ctx_alloc(qcomtee->ctx);
	if (!oic) {
		ret = -ENOMEM;
		goto out;
	}
	session->service = qcomtee_object_get_service(
		oic, session->client_env, LUMA_KEYMASTER_SERVICE_UID);
	kfree(oic);
	if (session->service == NULL_QCOMTEE_OBJECT) {
		pr_err("luma-keymaster: service uid=%u rejected\n",
		       LUMA_KEYMASTER_SERVICE_UID);
		goto out;
	}
	pr_info("luma-keymaster: service uid=%u opener accepted\n",
		LUMA_KEYMASTER_SERVICE_UID);

	args[0].b.addr = (void *)LUMA_KEYMASTER_PARTITION_APP;
	args[0].b.size = strlen(LUMA_KEYMASTER_PARTITION_APP);
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].type = QCOMTEE_ARG_TYPE_OO;
	ret = luma_control_invoke(qcomtee, session->service,
				  LUMA_KEYMASTER_INVOKE,
				  args, &result);
	if (ret || result) {
		pr_err("luma-keymaster: opener app=%s transport=%d result=%d\n",
		       LUMA_KEYMASTER_PARTITION_APP, ret, result);
		ret = ret ?: -EREMOTEIO;
		goto out;
	}
	session->app_client = args[1].o;
	pr_info("luma-keymaster: opener app=%s accepted\n",
		LUMA_KEYMASTER_PARTITION_APP);

	memset(args, 0, sizeof(args));
	args[0].b.addr = &selector;
	args[0].b.size = sizeof(selector);
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].type = QCOMTEE_ARG_TYPE_OO;
	result = QCOMTEE_MSG_ERROR_UNAVAIL;
	ret = luma_control_invoke(qcomtee, session->app_client,
				  LUMA_KEYMASTER_INVOKE,
				  args, &result);
	if (ret || result) {
		pr_err("luma-keymaster: selector=%u transport=%d result=%d\n",
		       selector, ret, result);
		ret = ret ?: -EREMOTEIO;
		goto out;
	}
	session->keymint_controller = args[1].o;
	pr_info("luma-keymaster: service uid=%u selector=%u accepted\n",
		LUMA_KEYMASTER_SERVICE_UID, selector);
	ret = luma_keymaster_prepare_client(
		qcomtee, session->keymint_controller, true);
	if (ret)
		goto out;

	/*
	 * KeyMint and Gatekeeper are separate, concurrently running Android
	 * services.  Each constructs its own authenticated client environment,
	 * UID-151 service, keymaster64 app client, and selector-150 controller.
	 * Reusing KeyMint's app client for Gatekeeper is not stock-equivalent,
	 * but neither is releasing KeyMint's initialized chain before Gatekeeper
	 * starts: Android keeps the KeyMint service and its controller alive.
	 */
	pr_info("luma-keymaster: KeyMint startup client retained while independent Gatekeeper opens\n");

	session->gatekeeper_client_env = luma_keymaster_get_client_env(qcomtee);
	if (session->gatekeeper_client_env == NULL_QCOMTEE_OBJECT) {
		ret = -EACCES;
		goto out;
	}
	oic = qcomtee_object_invoke_ctx_alloc(qcomtee->ctx);
	if (!oic) {
		ret = -ENOMEM;
		goto out;
	}
	session->gatekeeper_service = qcomtee_object_get_service(
		oic, session->gatekeeper_client_env, LUMA_KEYMASTER_SERVICE_UID);
	kfree(oic);
	if (session->gatekeeper_service == NULL_QCOMTEE_OBJECT) {
		pr_err("luma-keymaster: independent Gatekeeper service uid=%u rejected\n",
		       LUMA_KEYMASTER_SERVICE_UID);
		ret = -ENODEV;
		goto out;
	}
	pr_info("luma-keymaster: independent Gatekeeper service uid=%u opener accepted\n",
		LUMA_KEYMASTER_SERVICE_UID);

	memset(args, 0, sizeof(args));
	args[0].b.addr = (void *)LUMA_KEYMASTER_PARTITION_APP;
	args[0].b.size = strlen(LUMA_KEYMASTER_PARTITION_APP);
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].type = QCOMTEE_ARG_TYPE_OO;
	result = QCOMTEE_MSG_ERROR_UNAVAIL;
	ret = luma_control_invoke(qcomtee, session->gatekeeper_service,
				  LUMA_KEYMASTER_INVOKE, args, &result);
	if (ret || result) {
		pr_err("luma-keymaster: independent Gatekeeper opener app=%s transport=%d result=%d\n",
		       LUMA_KEYMASTER_PARTITION_APP, ret, result);
		ret = ret ?: -EREMOTEIO;
		goto out;
	}
	session->gatekeeper_app_client = args[1].o;
	pr_info("luma-keymaster: independent Gatekeeper opener app=%s accepted\n",
		LUMA_KEYMASTER_PARTITION_APP);

	memset(args, 0, sizeof(args));
	args[0].b.addr = &selector;
	args[0].b.size = sizeof(selector);
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].type = QCOMTEE_ARG_TYPE_OO;
	result = QCOMTEE_MSG_ERROR_UNAVAIL;
	ret = luma_control_invoke(qcomtee, session->gatekeeper_app_client,
				  LUMA_KEYMASTER_INVOKE, args, &result);
	if (ret || result) {
		pr_err("luma-keymaster: independent Gatekeeper selector=%u transport=%d result=%d\n",
		       selector, ret, result);
		ret = ret ?: -EREMOTEIO;
		goto out;
	}
	session->controller = args[1].o;
	ret = luma_keymaster_prepare_client(
		qcomtee, session->controller, false);
	if (ret)
		goto out;
	ret = 0;

out:
	if (ret) {
		luma_gatekeeper_release(qcomtee, session);
	} else {
		pr_info("luma-keymaster: authenticated controller retained for bounded Gatekeeper requests\n");
	}
	return ret;
}

static int luma_keymaster_get_encapsulated_key(
				struct qcomtee *qcomtee,
				struct qcomtee_object *controller,
				u8 **key, size_t *key_size)
{
	struct qcomtee_arg args[11] = { 0 };
	struct luma_keymaster_response *header;
	u8 request[LUMA_KEYMASTER_REQUEST_SIZE] = { 0 };
	u8 *response = NULL;
	u32 command = LUMA_KEYMASTER_GET_ENCAPSULATED_KEY;
	u32 authenticator_type = 2;
	u32 is_64 = 1;
	int ret = -ENODEV;
	int result = QCOMTEE_MSG_ERROR_UNAVAIL;

	if (controller == NULL_QCOMTEE_OBJECT || !key || !key_size)
		return -EINVAL;
	*key = NULL;
	*key_size = 0;
	response = kzalloc(LUMA_KEYMASTER_RESPONSE_SIZE, GFP_KERNEL);
	if (!response)
		return -ENOMEM;
	memcpy(request, &command, sizeof(command));
	memcpy(request + sizeof(command), &authenticator_type,
	       sizeof(authenticator_type));

	/* Exact QSEECom envelope used by the stock FP6 fingerprint HAL. */
	args[0].b.addr = request;
	args[0].b.size = sizeof(request);
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].b.addr = response;
	args[1].b.size = LUMA_KEYMASTER_RESPONSE_SIZE;
	args[1].type = QCOMTEE_ARG_TYPE_IB;
	args[2].b.addr = NULL;
	args[2].b.size = 0;
	args[2].type = QCOMTEE_ARG_TYPE_IB;
	args[3].b.addr = &is_64;
	args[3].b.size = sizeof(is_64);
	args[3].type = QCOMTEE_ARG_TYPE_IB;
	args[4].b.addr = request;
	args[4].b.size = sizeof(request);
	args[4].type = QCOMTEE_ARG_TYPE_OB;
	args[5].b.addr = response;
	args[5].b.size = LUMA_KEYMASTER_RESPONSE_SIZE;
	args[5].type = QCOMTEE_ARG_TYPE_OB;
	args[6].o = NULL_QCOMTEE_OBJECT;
	args[6].type = QCOMTEE_ARG_TYPE_IO;
	args[7].o = NULL_QCOMTEE_OBJECT;
	args[7].type = QCOMTEE_ARG_TYPE_IO;
	args[8].o = NULL_QCOMTEE_OBJECT;
	args[8].type = QCOMTEE_ARG_TYPE_IO;
	args[9].o = NULL_QCOMTEE_OBJECT;
	args[9].type = QCOMTEE_ARG_TYPE_IO;

	ret = luma_control_invoke(qcomtee, controller,
				  LUMA_QSEECOMCOMPAT_SEND_REQUEST,
				  args, &result);
	if (ret || result) {
		pr_err("luma-keymaster: stock command=0x%x transport=%d result=%d\n",
		       command, ret, result);
		ret = ret ?: -EREMOTEIO;
		goto out;
	}
	if (args[5].b.size < sizeof(*header)) {
		ret = -EPROTO;
		goto out;
	}
	header = (void *)response;
	if (header->status) {
		pr_err("luma-keymaster: stock command=0x%x status=%d\n",
		       command, header->status);
		ret = -EREMOTEIO;
		goto out;
	}
	if (header->blob_offset < sizeof(*header) ||
	    header->blob_offset > args[5].b.size ||
	    header->blob_length > args[5].b.size - header->blob_offset) {
		pr_err("luma-keymaster: stock command=0x%x malformed offset=%u length=%u bytes=%zu\n",
		       command, header->blob_offset, header->blob_length,
		       args[5].b.size);
		ret = -EPROTO;
		goto out;
	}
	if (!header->blob_length) {
		ret = -ENODATA;
		goto out;
	}
	*key = kmemdup(response + header->blob_offset,
		       header->blob_length, GFP_KERNEL);
	if (!*key) {
		ret = -ENOMEM;
		goto out;
	}
	*key_size = header->blob_length;
	pr_info("luma-keymaster: stock command=0x%x accepted blob_bytes=%zu contents_logged=0\n",
		command, *key_size);
	ret = 0;

out:
	memzero_explicit(response, LUMA_KEYMASTER_RESPONSE_SIZE);
	kfree(response);
	memzero_explicit(request, sizeof(request));
	return ret;
}

static int luma_keymaster_load(struct qcomtee *qcomtee, struct device *dev,
			       struct qcomtee_object *loader,
			       struct qcomtee_object **controller,
			       bool *loaded_by_us);
static int luma_keymaster_unload(struct qcomtee *qcomtee,
				 struct qcomtee_object **controller,
				 bool loaded_by_us);
static int luma_focal_secure_prepare(struct qcomtee *qcomtee,
				     struct device *dev,
				     struct qcomtee_object *loader,
				     struct qcomtee_object *controller,
				     struct luma_gatekeeper_session *gatekeeper)
{
	struct qcomtee_object *keymaster_controller = NULL_QCOMTEE_OBJECT;
	u8 *encapsulated_key = NULL;
	size_t encapsulated_key_size = 0;
	size_t response_size = 0;
	bool keymaster_loaded_by_us = false;
	int ret, unload_ret;

	ret = luma_keymaster_load(qcomtee, dev, loader,
				  &keymaster_controller,
				  &keymaster_loaded_by_us);
	if (ret) {
		luma_keymaster_unload(qcomtee, &keymaster_controller,
				      keymaster_loaded_by_us);
		return ret;
	}
	/* Stock ordering on the authenticated Keymaster service controller. */
	/* Consume the truthful Root-of-Trust state established by the boot chain. */
	ret = luma_keymaster_prepare_service(qcomtee, gatekeeper);
	if (ret)
		goto out_keymaster;
	/*
	 * Match the stock FP6 fingerprint HAL exactly.  The FocalTech bootstrap
	 * opens the legacy QSEECom keymaster application and sends command 0x205
	 * directly.  The returned inter-app encapsulation is opaque and is
	 * forwarded unchanged to focal64.
	 */
	ret = luma_keymaster_get_encapsulated_key(qcomtee,
					  keymaster_controller,
					  &encapsulated_key,
					  &encapsulated_key_size);
out_keymaster:
	unload_ret = luma_keymaster_unload(qcomtee, &keymaster_controller,
					    keymaster_loaded_by_us);
	if (ret || unload_ret) {
		luma_gatekeeper_release(qcomtee, gatekeeper);
		if (encapsulated_key) {
			memzero_explicit(encapsulated_key,
					 encapsulated_key_size);
			kfree(encapsulated_key);
		}
		return ret ?: unload_ret;
	}
	if (!encapsulated_key || !encapsulated_key_size)
		goto missing_key;
	ret = luma_focal_exchange(qcomtee, controller,
				  LUMA_FOCAL_SYNC_ENCAPSULATED_KEY,
				  encapsulated_key, encapsulated_key_size,
				  NULL, 0, &response_size);
	memzero_explicit(encapsulated_key, encapsulated_key_size);
	kfree(encapsulated_key);
	if (ret)
		goto prepare_failed;
	if (response_size)
		goto protocol_failed;

	if (sizeof(luma_focal_stock_config) != LUMA_FOCAL_STOCK_CONFIG_SIZE ||
	    luma_focal_stock_config[LUMA_FOCAL_STOCK_CONFIG_SIZE - 1] != '\0')
		goto identity_failed;
	ret = luma_focal_exchange(qcomtee, controller,
				  LUMA_FOCAL_SYNC_CONFIG,
				  luma_focal_stock_config,
				  sizeof(luma_focal_stock_config),
				  NULL, 0, &response_size);
	if (ret)
		goto prepare_failed;
	if (response_size)
		goto protocol_failed;
	pr_info("luma-focal: stock configuration accepted bytes=%zu sha256=%s\n",
		sizeof(luma_focal_stock_config),
		LUMA_FOCAL_STOCK_CONFIG_SHA256);

	ret = luma_focal_exchange(qcomtee, controller,
				  LUMA_FOCAL_INITIALIZE_SPI,
				  NULL, 0, NULL, 0, &response_size);
	if (ret)
		goto prepare_failed;
	if (response_size)
		goto protocol_failed;

	return response_size ? -EPROTO : 0;

missing_key:
	ret = -ENODATA;
	goto prepare_failed;
protocol_failed:
	ret = -EPROTO;
	goto prepare_failed;
identity_failed:
	ret = -EKEYREJECTED;
prepare_failed:
	luma_gatekeeper_release(qcomtee, gatekeeper);
	return ret;
}

static int luma_focal_probe_and_initialize_device(
					struct qcomtee *qcomtee,
					struct qcomtee_object *controller)
{
	u8 probe_data[LUMA_FOCAL_DEVICE_DATA_BYTES] = { 0 };
	u8 device_data[LUMA_FOCAL_DEVICE_DATA_BYTES] = { 0 };
	u8 force = 1;
	size_t response_size = 0;
	s32 trustlet_status = 0;
	int ret;

	ret = luma_focal_exchange_status(qcomtee, controller,
					 LUMA_FOCAL_PROBE_DEVICE,
					 &force, sizeof(force), probe_data,
					 sizeof(probe_data), &response_size,
					 &trustlet_status, NULL, NULL, NULL);
	if (ret == -EREMOTEIO &&
	    trustlet_status == LUMA_FOCAL_PROBE_RETRY_STATUS) {
		ret = -EAGAIN;
		goto out;
	}
	if (ret)
		goto out;
	if (response_size != sizeof(probe_data)) {
		ret = -EPROTO;
		goto out;
	}
	pr_info("luma-focal: physical probe accepted opaque_bytes=%zu raw_images=0\n",
		response_size);

	ret = luma_focal_exchange(qcomtee, controller,
				  LUMA_FOCAL_INITIALIZE_DEVICE,
				  NULL, 0, device_data,
				  sizeof(device_data), &response_size);
	if (ret)
		goto out;
	if (response_size != sizeof(device_data)) {
		ret = -EPROTO;
		goto out;
	}
	pr_info("luma-focal: device initialization accepted opaque_bytes=%zu raw_images=0\n",
		response_size);
	ret = 0;
out:
	memzero_explicit(device_data, sizeof(device_data));
	memzero_explicit(probe_data, sizeof(probe_data));
	return ret;
}

static int luma_focal_initialize_and_enumerate(
					struct qcomtee *qcomtee,
					struct qcomtee_object *controller)
{
	u8 initialization[LUMA_FOCAL_INITIALIZATION_BYTES] = { 0 };
	u8 templates[sizeof(u32) +
		LUMA_FOCAL_MAX_TEMPLATES * 2 * sizeof(u32)] = { 0 };
	u32 capacity = LUMA_FOCAL_MAX_TEMPLATES;
	u32 count;
	size_t response_size = 0;
	int ret;

	ret = luma_focal_exchange(qcomtee, controller, LUMA_FOCAL_INITIALIZE,
				  NULL, 0, initialization,
				  sizeof(initialization), &response_size);
	if (ret)
		goto out;
	if (response_size != sizeof(initialization)) {
		ret = -EPROTO;
		goto out;
	}

	ret = luma_focal_exchange(qcomtee, controller, LUMA_FOCAL_ENUMERATE,
				  &capacity, sizeof(capacity), templates,
				  sizeof(templates), &response_size);
	if (ret)
		goto out;
	if (response_size < sizeof(count)) {
		ret = -EPROTO;
		goto out;
	}
	memcpy(&count, templates, sizeof(count));
	if (count > LUMA_FOCAL_MAX_TEMPLATES ||
	    response_size != sizeof(count) + count * 2 * sizeof(u32)) {
		ret = -EPROTO;
		goto out;
	}
	pr_info("luma-focal: enumeration accepted count=%u raw_images=0 templates_written=0\n",
		count);
	ret = 0;
out:
	memzero_explicit(templates, sizeof(templates));
	memzero_explicit(initialization, sizeof(initialization));
	return ret;
}

static const char *luma_focal_stage_name(enum luma_focal_stage stage)
{
	switch (stage) {
	case LUMA_FOCAL_STAGE_IDLE:
		return "idle";
	case LUMA_FOCAL_STAGE_SECURE_PREPARED:
		return "secure-prepared";
	case LUMA_FOCAL_STAGE_PROBE_RETRY:
		return "probe-retry";
	case LUMA_FOCAL_STAGE_DEVICE_INITIALIZED:
		return "device-initialized";
	case LUMA_FOCAL_STAGE_COMPLETE:
		return "complete";
	case LUMA_FOCAL_STAGE_FAILED:
		return "failed";
	default:
		return "invalid";
	}
}

static int luma_focal_release_locked(bool free_spi)
{
	struct qcomtee_arg args[1] = { 0 };
	struct luma_focal_session *session = &luma_focal_session;
	size_t response_size = 0;
	int first_error = 0;
	int result = QCOMTEE_MSG_ERROR_UNAVAIL;
	int ret;

	luma_listener_stop_all();
	luma_focal_capture_buffers_clear(session);

	if (session->controller != NULL_QCOMTEE_OBJECT &&
	    session->qcomtee && free_spi && session->spi_initialized) {
		ret = luma_focal_exchange(session->qcomtee, session->controller,
					  LUMA_FOCAL_FREE_SPI,
					  NULL, 0, NULL, 0,
					  &response_size);
		if (ret || response_size) {
			pr_err("luma-focal: free-SPI failed=%d response=%zu\n",
				ret, response_size);
			first_error = ret ?: -EPROTO;
		}
	}
	session->spi_initialized = false;
	if (session->controller != NULL_QCOMTEE_OBJECT && session->qcomtee) {
		ret = luma_control_invoke(session->qcomtee, session->controller,
					  LUMA_CONTROL_UNLOAD, args, &result);
		if (ret || result) {
			pr_err("luma-control: staged unload transport=%d result=%d\n",
				ret, result);
			if (!first_error)
				first_error = ret ?: -EREMOTEIO;
		} else {
			pr_info("luma-control: staged focal TA unloaded\n");
		}
	}
	if (session->qcomtee) {
		luma_gatekeeper_release(session->qcomtee, &session->gatekeeper);
		luma_control_put_sync(session->qcomtee, &session->controller);
		luma_control_put_sync(session->qcomtee, &session->loader);
		luma_control_put_sync(session->qcomtee, &session->client_env);
		luma_listener_release_all(session->qcomtee);
	}
	session->qcomtee = NULL;
	return first_error;
}

static ssize_t luma_focal_phase_show(struct device *dev,
				     struct device_attribute *attribute,
				     char *buffer)
{
	const char *name;

	(void)attribute;
	mutex_lock(&luma_focal_session_lock);
	if (luma_focal_session.attribute_created &&
	    luma_focal_session.dev && luma_focal_session.dev != dev)
		name = "invalid";
	else
		name = luma_focal_stage_name(luma_focal_session.stage);
	mutex_unlock(&luma_focal_session_lock);
	return sysfs_emit(buffer, "%s\n", name);
}

static ssize_t luma_focal_phase_store(struct device *dev,
				      struct device_attribute *attribute,
				      const char *buffer, size_t count)
{
	struct luma_focal_session *session = &luma_focal_session;
	int cleanup_ret = 0;
	int ret = 0;

	(void)attribute;
	if (!capable(CAP_SYS_ADMIN))
		return -EPERM;
	mutex_lock(&luma_focal_session_lock);
	if (!session->attribute_created || session->dev != dev ||
	    !session->qcomtee ||
	    session->controller == NULL_QCOMTEE_OBJECT) {
		ret = -ENODEV;
		goto out;
	}
	if (sysfs_streq(buffer, "probe")) {
		if (session->stage != LUMA_FOCAL_STAGE_SECURE_PREPARED &&
		    session->stage != LUMA_FOCAL_STAGE_PROBE_RETRY) {
			ret = -EPERM;
			goto out;
		}
		ret = luma_focal_probe_and_initialize_device(
			session->qcomtee, session->controller);
		if (!ret) {
			session->stage = LUMA_FOCAL_STAGE_DEVICE_INITIALIZED;
			pr_info("luma-focal: phase=device-initialized; waiting for normal-world IRQ finalization\n");
			goto out;
		}
		if (ret == -EAGAIN && !session->probe_retries) {
			session->probe_retries = 1;
			session->stage = LUMA_FOCAL_STAGE_PROBE_RETRY;
			pr_info("luma-focal: phase=probe-retry status=%d; one bounded power-cycle allowed\n",
				LUMA_FOCAL_PROBE_RETRY_STATUS);
			goto out;
		}
	} else if (sysfs_streq(buffer, "enumerate")) {
		if (session->stage != LUMA_FOCAL_STAGE_DEVICE_INITIALIZED) {
			ret = -EPERM;
			goto out;
		}
		ret = luma_focal_initialize_and_enumerate(
			session->qcomtee, session->controller);
		if (!ret)
			session->stage = LUMA_FOCAL_STAGE_COMPLETE;
	} else if (sysfs_streq(buffer, "abort")) {
		ret = -ECANCELED;
	} else {
		ret = -EINVAL;
		goto out;
	}

	if (ret)
		session->stage = LUMA_FOCAL_STAGE_FAILED;
	cleanup_ret = luma_focal_release_locked(true);
	if (!ret && cleanup_ret) {
		session->stage = LUMA_FOCAL_STAGE_FAILED;
		ret = cleanup_ret;
	}
out:
	mutex_unlock(&luma_focal_session_lock);
	return ret ? ret : (ssize_t)count;
}

static DEVICE_ATTR_ADMIN_RW(luma_focal_phase);

static int luma_control_fw_name(const char *app, char *name,
				size_t name_size, unsigned int split)
{
	int count;

	count = snprintf(name, name_size, "%s.b%02u", app, split);
	return count < 0 || (size_t)count >= name_size ? -EINVAL : 0;
}

static int luma_control_reconstruct(struct device *dev, const char *app,
				    u8 **elf, size_t *elf_size)
{
	const struct firmware *header = NULL, *last = NULL, *part = NULL;
	Elf64_Ehdr ehdr;
	size_t offsets[LUMA_CONTROL_SPLITS] = { 0 };
	char name[32];
	unsigned int i;
	int ret;

	ret = luma_control_fw_name(app, name, sizeof(name), 0);
	if (ret)
		return ret;
	ret = request_firmware_direct(&header, name, dev);
	if (ret)
		return ret;
	if (header->size < sizeof(ehdr)) {
		ret = -EINVAL;
		goto out;
	}
	memcpy(&ehdr, header->data, sizeof(ehdr));
	if (memcmp(ehdr.e_ident, ELFMAG, SELFMAG) ||
	    ehdr.e_ident[EI_CLASS] != ELFCLASS64 ||
	    ehdr.e_phnum != LUMA_CONTROL_SPLITS ||
	    ehdr.e_phentsize != sizeof(Elf64_Phdr) ||
	    ehdr.e_phoff > header->size ||
	    LUMA_CONTROL_SPLITS >
		(header->size - ehdr.e_phoff) / sizeof(Elf64_Phdr)) {
		ret = -EINVAL;
		goto out;
	}
	for (i = 1; i < LUMA_CONTROL_SPLITS; i++) {
		Elf64_Phdr phdr;

		memcpy(&phdr, header->data + ehdr.e_phoff +
		       i * sizeof(phdr), sizeof(phdr));
		if (phdr.p_offset > LUMA_CONTROL_MAX_ELF) {
			ret = -EFBIG;
			goto out;
		}
		offsets[i] = phdr.p_offset;
	}

	ret = luma_control_fw_name(app, name, sizeof(name),
				   LUMA_CONTROL_SPLITS - 1);
	if (ret)
		goto out;
	ret = request_firmware_direct(&last, name, dev);
	if (ret)
		goto out;
	if (last->size > LUMA_CONTROL_MAX_ELF ||
	    offsets[LUMA_CONTROL_SPLITS - 1] >
	    LUMA_CONTROL_MAX_ELF - last->size) {
		ret = -EFBIG;
		goto out;
	}
	*elf_size = offsets[LUMA_CONTROL_SPLITS - 1] + last->size;
	*elf = vzalloc(*elf_size);
	if (!*elf) {
		ret = -ENOMEM;
		goto out;
	}
	if (header->size > *elf_size) {
		ret = -EINVAL;
		goto out_free;
	}
	memcpy(*elf, header->data, header->size);

	for (i = 1; i < LUMA_CONTROL_SPLITS - 1; i++) {
		ret = luma_control_fw_name(app, name, sizeof(name), i);
		if (ret)
			goto out_free;
		ret = request_firmware_direct(&part, name, dev);
		if (ret)
			goto out_free;
		if (offsets[i] > *elf_size ||
		    part->size > *elf_size - offsets[i]) {
			ret = -EINVAL;
			release_firmware(part);
			part = NULL;
			goto out_free;
		}
		memcpy(*elf + offsets[i], part->data, part->size);
		release_firmware(part);
		part = NULL;
	}
	memcpy(*elf + offsets[LUMA_CONTROL_SPLITS - 1], last->data,
	       last->size);
	ret = 0;
	goto out;

out_free:
	vfree(*elf);
	*elf = NULL;
	*elf_size = 0;
out:
	release_firmware(part);
	release_firmware(last);
	release_firmware(header);
	return ret;
}

static int luma_keymaster_validate_elf(const struct firmware *firmware)
{
	Elf64_Ehdr ehdr;
	Elf64_Phdr phdr;
	size_t end, max_end = 0;
	unsigned int i;

	if (!firmware || firmware->size != LUMA_KEYMASTER_PARTITION_SIZE ||
	    firmware->size < sizeof(ehdr))
		return -EINVAL;
	memcpy(&ehdr, firmware->data, sizeof(ehdr));
	if (memcmp(ehdr.e_ident, ELFMAG, SELFMAG) ||
	    ehdr.e_ident[EI_CLASS] != ELFCLASS64 ||
	    ehdr.e_ident[EI_DATA] != ELFDATA2LSB ||
	    ehdr.e_machine != EM_AARCH64 ||
	    ehdr.e_ehsize != sizeof(ehdr) ||
	    ehdr.e_phnum != LUMA_CONTROL_SPLITS ||
	    ehdr.e_phentsize != sizeof(phdr) ||
	    ehdr.e_phoff > firmware->size ||
	    ehdr.e_phnum >
		(firmware->size - ehdr.e_phoff) / sizeof(phdr))
		return -EINVAL;

	for (i = 0; i < ehdr.e_phnum; i++) {
		memcpy(&phdr, firmware->data + ehdr.e_phoff +
		       i * sizeof(phdr), sizeof(phdr));
		if (phdr.p_offset > firmware->size ||
		    phdr.p_filesz > firmware->size - phdr.p_offset)
			return -EINVAL;
		end = phdr.p_offset + phdr.p_filesz;
		max_end = max(max_end, end);
	}
	return max_end == firmware->size ? 0 : -EINVAL;
}

static int luma_keymaster_load(struct qcomtee *qcomtee, struct device *dev,
			       struct qcomtee_object *loader,
			       struct qcomtee_object **controller,
			       bool *loaded_by_us)
{
	const struct firmware *firmware = NULL;
	struct qcomtee_arg args[4] = { 0 };
	char distinguished_name[LUMA_CONTROL_DIST_NAME_SIZE] = { 0 };
	u32 arch = 0;
	int ret, result = QCOMTEE_MSG_ERROR_UNAVAIL;

	*controller = NULL_QCOMTEE_OBJECT;
	*loaded_by_us = false;
	args[0].b.addr = (void *)LUMA_KEYMASTER_PARTITION_APP;
	args[0].b.size = strlen(LUMA_KEYMASTER_PARTITION_APP);
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].b.addr = &arch;
	args[1].b.size = sizeof(arch);
	args[1].type = QCOMTEE_ARG_TYPE_OB;
	args[2].type = QCOMTEE_ARG_TYPE_OO;
	ret = luma_control_invoke(qcomtee, loader, LUMA_CONTROL_LOOKUP_TA,
				  args, &result);
	if (ret) {
		pr_err("luma-keymaster: QSEE lookup transport=%d\n", ret);
		return ret;
	}
	if (!result) {
		*controller = args[2].o;
		pr_info("luma-keymaster: QSEE identity already resident app=%s arch=%u\n",
			LUMA_KEYMASTER_PARTITION_APP, arch);
		return 0;
	}
	else if (result != LUMA_CONTROL_NOT_LOADED) {
		pr_err("luma-keymaster: QSEE lookup result=%d\n", result);
		return -EREMOTEIO;
	}

	ret = request_firmware_direct(&firmware,
				      LUMA_KEYMASTER_PARTITION_FW, dev);
	if (ret) {
		pr_err("luma-keymaster: signed ELF request failed=%d\n", ret);
		return ret;
	}
	ret = luma_keymaster_validate_elf(firmware);
	if (ret) {
		pr_err("luma-keymaster: signed ELF identity rejected bytes=%zu\n",
		       firmware->size);
		goto out;
	}

	memset(args, 0, sizeof(args));
	args[0].b.addr = (void *)firmware->data;
	args[0].b.size = firmware->size;
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].b.addr = (void *)LUMA_KEYMASTER_PARTITION_APP;
	args[1].b.size = strlen(LUMA_KEYMASTER_PARTITION_APP);
	args[1].type = QCOMTEE_ARG_TYPE_IB;
	args[2].b.addr = distinguished_name;
	args[2].b.size = sizeof(distinguished_name);
	args[2].type = QCOMTEE_ARG_TYPE_OB;
	args[3].type = QCOMTEE_ARG_TYPE_OO;
	result = QCOMTEE_MSG_ERROR_UNAVAIL;
	ret = luma_control_invoke(qcomtee, loader,
				  LUMA_CONTROL_LOAD_FROM_BUFFER, args, &result);
	if (ret || result) {
		pr_err("luma-keymaster: QSEE load transport=%d result=%d elf_bytes=%zu\n",
		       ret, result, firmware->size);
		ret = ret ?: -EREMOTEIO;
		goto out;
	}
	*controller = args[3].o;
	*loaded_by_us = true;
	if (args[2].b.size != strlen(LUMA_KEYMASTER_PARTITION_APP) ||
	    memcmp(distinguished_name, LUMA_KEYMASTER_PARTITION_APP,
		   strlen(LUMA_KEYMASTER_PARTITION_APP))) {
		pr_err("luma-keymaster: QSEE distinguished identity mismatch bytes=%zu\n",
		       args[2].b.size);
		ret = -EKEYREJECTED;
		goto out;
	}
	pr_info("luma-keymaster: QSEE identity accepted app=%s distinguished_name=%s elf_bytes=%zu partition_read_only=1\n",
		LUMA_KEYMASTER_PARTITION_APP, LUMA_KEYMASTER_PARTITION_APP,
		firmware->size);
	ret = 0;

out:
	release_firmware(firmware);
	return ret;
}

static int luma_keymaster_unload(struct qcomtee *qcomtee,
				 struct qcomtee_object **controller,
				 bool loaded_by_us)
{
	struct qcomtee_arg args[1] = { 0 };
	int ret = 0, result = QCOMTEE_MSG_ERROR_UNAVAIL;

	if (*controller == NULL_QCOMTEE_OBJECT)
		return 0;
	if (loaded_by_us) {
		ret = luma_control_invoke(qcomtee, *controller,
					  LUMA_CONTROL_UNLOAD, args, &result);
		if (ret || result) {
			pr_err("luma-keymaster: QSEE unload transport=%d result=%d\n",
			       ret, result);
			ret = ret ?: -EREMOTEIO;
		} else {
			pr_info("luma-keymaster: QSEE trustlet unloaded\n");
		}
	} else {
		pr_info("luma-keymaster: QSEE trustlet preserved preexisting=1\n");
	}
	luma_control_put_sync(qcomtee, controller);
	return ret;
}

void luma_qcomtee_control_identity(struct qcomtee *qcomtee, struct device *dev)
{
	struct qcomtee_object *client_env = NULL_QCOMTEE_OBJECT;
	struct qcomtee_object *loader = NULL_QCOMTEE_OBJECT;
	struct qcomtee_object *controller = NULL_QCOMTEE_OBJECT;
	struct qcomtee_object_invoke_ctx *oic;
	struct qcomtee_arg args[5] = { 0 };
	char distinguished_name[LUMA_CONTROL_DIST_NAME_SIZE] = { 0 };
	u32 arch = 0;
	u8 *elf = NULL;
	size_t elf_size = 0;
	bool staged = false;
	int ret, result = QCOMTEE_MSG_ERROR_UNAVAIL;
	const char *app = luma_keymaster_lookup ? LUMA_KEYMASTER_APP :
		(luma_focal_identity || luma_focal_enumerate) ?
		LUMA_FINGERPRINT_APP : LUMA_CONTROL_APP;
	u32 loader_uid = luma_standard_identity ?
		LUMA_CONTROL_STANDARD_APP_LOADER_UID :
		LUMA_CONTROL_APP_LOADER_UID;

	if (!luma_control_identity && !luma_standard_identity &&
	    !luma_focal_identity && !luma_focal_enumerate &&
	    !luma_keymaster_lookup)
		return;
	if ((luma_control_identity ? 1 : 0) +
	    (luma_standard_identity ? 1 : 0) +
	    (luma_focal_identity ? 1 : 0) +
	    (luma_focal_enumerate ? 1 : 0) +
	    (luma_keymaster_lookup ? 1 : 0) != 1) {
		pr_err("luma-control: mutually exclusive identity controls requested\n");
		return;
	}

	pr_info("luma-control: begin app=%s loader_uid=%u ta_commands=0 biometric_commands=0\n",
		app, loader_uid);
	oic = qcomtee_object_invoke_ctx_alloc(qcomtee->ctx);
	if (!oic) {
		pr_err("luma-control: client environment allocation failed\n");
		return;
	}
	client_env = qcomtee_object_get_client_env(oic);
	kfree(oic);
	if (client_env == NULL_QCOMTEE_OBJECT) {
		pr_err("luma-control: privileged client environment rejected\n");
		goto out;
	}
	pr_info("luma-control: privileged client environment accepted\n");

	oic = qcomtee_object_invoke_ctx_alloc(qcomtee->ctx);
	if (!oic) {
		pr_err("luma-control: service allocation failed\n");
		goto out;
	}
	loader = qcomtee_object_get_service(oic, client_env, loader_uid);
	kfree(oic);
	if (loader == NULL_QCOMTEE_OBJECT) {
		pr_err("luma-control: service uid=%u rejected\n", loader_uid);
		goto out;
	}
	pr_info("luma-control: service uid=%u accepted\n", loader_uid);

	if (luma_standard_identity) {
		ret = luma_control_reconstruct(dev, app, &elf, &elf_size);
		if (ret) {
			pr_err("luma-control: reconstruction failed=%d\n", ret);
			goto out;
		}
		args[0].b.addr = elf;
		args[0].b.size = elf_size;
		args[0].type = QCOMTEE_ARG_TYPE_IB;
		args[1].type = QCOMTEE_ARG_TYPE_OO;
		ret = luma_control_invoke(qcomtee, loader,
					  LUMA_CONTROL_STANDARD_LOAD_FROM_BUFFER,
					  args, &result);
		if (ret || result) {
			pr_err("luma-control: standard load transport=%d result=%d elf_bytes=%zu\n",
			       ret, result, elf_size);
			goto out;
		}
		controller = args[1].o;
		pr_info("luma-control: standard identity accepted app=%s elf_bytes=%zu; no TA operation invoked\n",
			app, elf_size);
		memset(args, 0, sizeof(args));
		ret = luma_control_invoke(qcomtee, controller,
					  LUMA_CONTROL_STANDARD_UNLOAD,
					  args, &result);
		if (ret || result)
			pr_err("luma-control: standard unload transport=%d result=%d\n",
			       ret, result);
		else
			pr_info("luma-control: standard control TA unloaded\n");
		goto out;
	}

	args[0].b.addr = (void *)app;
	args[0].b.size = strlen(app);
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].b.addr = &arch;
	args[1].b.size = sizeof(arch);
	args[1].type = QCOMTEE_ARG_TYPE_OB;
	args[2].type = QCOMTEE_ARG_TYPE_OO;
	ret = luma_control_invoke(qcomtee, loader, LUMA_CONTROL_LOOKUP_TA,
				  args, &result);
	if (ret) {
		pr_err("luma-control: lookup transport=%d\n", ret);
		goto out;
	}
	if (!result) {
		controller = args[2].o;
		if (luma_keymaster_lookup) {
			pr_info("luma-control: keymaster lookup accepted arch=%u ta_commands=0\n",
				arch);
			goto out;
		}
		pr_err("luma-control: app unexpectedly preloaded arch=%u\n", arch);
		goto out;
	}
	if (luma_keymaster_lookup) {
		pr_info("luma-control: keymaster lookup unavailable result=%d ta_commands=0\n",
			result);
		goto out;
	}
	if (result != LUMA_CONTROL_NOT_LOADED) {
		pr_err("luma-control: lookup result=%d\n", result);
		goto out;
	}
	pr_info("luma-control: lookup result=%d app=%s\n", result,
		app);

	ret = luma_control_reconstruct(dev, app, &elf, &elf_size);
	if (ret) {
		pr_err("luma-control: reconstruction failed=%d\n", ret);
		goto out;
	}
	memset(args, 0, sizeof(args));
	args[0].b.addr = elf;
	args[0].b.size = elf_size;
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].b.addr = (void *)app;
	args[1].b.size = strlen(app);
	args[1].type = QCOMTEE_ARG_TYPE_IB;
	args[2].b.addr = distinguished_name;
	args[2].b.size = sizeof(distinguished_name);
	args[2].type = QCOMTEE_ARG_TYPE_OB;
	args[3].type = QCOMTEE_ARG_TYPE_OO;
	ret = luma_control_invoke(qcomtee, loader,
				  LUMA_CONTROL_LOAD_FROM_BUFFER, args, &result);
	if (ret || result) {
		pr_err("luma-control: load transport=%d result=%d elf_bytes=%zu\n",
		       ret, result, elf_size);
		goto out;
	}
	controller = args[3].o;
	if (luma_focal_enumerate)
		pr_info("luma-control: identity accepted app=%s distinguished_name=%.*s elf_bytes=%zu; bounded TA operations requested\n",
			app, (int)args[2].b.size, distinguished_name,
			elf_size);
	else
		pr_info("luma-control: identity accepted app=%s distinguished_name=%.*s elf_bytes=%zu; no TA operation invoked\n",
			app, (int)args[2].b.size, distinguished_name,
			elf_size);
	if (luma_focal_enumerate) {
		ret = luma_focal_secure_prepare(
			qcomtee, dev, loader, controller,
			&luma_focal_session.gatekeeper);
		if (ret) {
			pr_err("luma-focal: secure preparation failed=%d\n", ret);
			goto unload;
		}
		mutex_lock(&luma_focal_session_lock);
		if (luma_focal_session.stage != LUMA_FOCAL_STAGE_IDLE ||
		    luma_focal_session.attribute_created) {
			ret = -EBUSY;
			mutex_unlock(&luma_focal_session_lock);
			pr_err("luma-focal: staged session already active\n");
			goto unload;
		}
		luma_focal_session.qcomtee = qcomtee;
		luma_focal_session.dev = dev;
		luma_focal_session.client_env = client_env;
		luma_focal_session.loader = loader;
		luma_focal_session.controller = controller;
		luma_focal_session.stage = LUMA_FOCAL_STAGE_SECURE_PREPARED;
		luma_focal_session.probe_retries = 0;
		luma_focal_session.spi_initialized = true;
		if (luma_focal_enroll_service) {
			ret = luma_listener_register_all(&luma_focal_session);
			if (ret) {
				luma_focal_session.stage = LUMA_FOCAL_STAGE_FAILED;
				luma_focal_release_locked(true);
				mutex_unlock(&luma_focal_session_lock);
				client_env = NULL_QCOMTEE_OBJECT;
				loader = NULL_QCOMTEE_OBJECT;
				controller = NULL_QCOMTEE_OBJECT;
				pr_err("luma-focal: QSEEComCompat listener registration failed=%d\n",
				       ret);
				goto out;
			}
		}
		ret = device_create_file(dev, &dev_attr_luma_focal_phase);
		if (ret) {
			luma_focal_session.stage = LUMA_FOCAL_STAGE_FAILED;
			luma_focal_release_locked(true);
			mutex_unlock(&luma_focal_session_lock);
			client_env = NULL_QCOMTEE_OBJECT;
			loader = NULL_QCOMTEE_OBJECT;
			controller = NULL_QCOMTEE_OBJECT;
			pr_err("luma-focal: staged phase control creation failed=%d\n",
			       ret);
			goto out;
		}
		luma_focal_session.attribute_created = true;
		client_env = NULL_QCOMTEE_OBJECT;
		loader = NULL_QCOMTEE_OBJECT;
		controller = NULL_QCOMTEE_OBJECT;
		staged = true;
		mutex_unlock(&luma_focal_session_lock);
		pr_info("luma-focal: phase=secure-prepared; waiting for bounded physical probe\n");
		goto out;
	}

unload:
	memset(args, 0, sizeof(args));
	ret = luma_control_invoke(qcomtee, controller, LUMA_CONTROL_UNLOAD,
				  args, &result);
	if (ret || result)
		pr_err("luma-control: unload transport=%d result=%d\n", ret,
		       result);
	else
		pr_info("luma-control: control TA unloaded\n");

out:
	vfree(elf);
	luma_control_put_sync(qcomtee, &controller);
	luma_control_put_sync(qcomtee, &loader);
	luma_control_put_sync(qcomtee, &client_env);
	if (staged)
		pr_info("luma-control: staged stock_initialization=true biometric_commands=enumerate-only raw_images=0 templates_written=0\n");
	else
		pr_info("luma-control: complete biometric_commands=0\n");
}

void luma_qcomtee_control_cleanup(struct qcomtee *qcomtee, struct device *dev)
{
	bool remove_attribute = false;
	int ret = 0;

	mutex_lock(&luma_focal_session_lock);
	if (luma_focal_session.attribute_created &&
	    luma_focal_session.dev == dev) {
		luma_focal_session.attribute_created = false;
		remove_attribute = true;
	}
	mutex_unlock(&luma_focal_session_lock);
	if (remove_attribute)
		device_remove_file(dev, &dev_attr_luma_focal_phase);

	mutex_lock(&luma_focal_session_lock);
	if (luma_focal_session.qcomtee == qcomtee) {
		if (luma_focal_session.stage != LUMA_FOCAL_STAGE_COMPLETE)
			luma_focal_session.stage = LUMA_FOCAL_STAGE_FAILED;
		ret = luma_focal_release_locked(true);
	}
	luma_focal_session.dev = NULL;
	mutex_unlock(&luma_focal_session_lock);
	if (ret)
		pr_err("luma-focal: removal cleanup failed=%d\n", ret);
}
