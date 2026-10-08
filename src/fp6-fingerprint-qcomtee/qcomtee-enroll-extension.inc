/* SPDX-License-Identifier: GPL-2.0-only */

/*
 * Root-only, bounded transport for Luma's host-tested enrollment protocol.
 * This file is included into qcomtee-control.c by the v23 build recipe so it
 * can reuse the already-audited QTEE object/session helpers without exporting
 * them from the kernel module. It never logs request or response contents.
 */

#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>

#include "luma-fp6-enrollment-abi.h"

#define LUMA_GATEKEEPER_ENROLL 0x00021001U
#define LUMA_GATEKEEPER_VERIFY 0x00021002U
#define LUMA_GATEKEEPER_DELETE_USER 0x00021003U
#define LUMA_GATEKEEPER_DELETE_ALL_USERS 0x00021004U
#define LUMA_FOCAL_PRE_ENROLL 0x2000U
#define LUMA_FOCAL_ENROLL 0x2001U
#define LUMA_FOCAL_POST_ENROLL 0x2002U
#define LUMA_FOCAL_CANCEL 0x2004U
#define LUMA_FOCAL_ENUMERATE_ALLOWED 0x2005U
#define LUMA_FOCAL_SET_ACTIVE_GROUP 0x2007U
#define LUMA_FOCAL_SYNC_STATISTICS 0x100eU
#define LUMA_FOCAL_SYNC_TEMPLATE 0x1010U
#define LUMA_FOCAL_STATISTICS_SIZE 560U
#define LUMA_FOCAL_ACTIVE_GROUP_PATH "/data/vendor_de/0/fpdata"
#define LUMA_FOCAL_CAPTURE_IMAGE 0x1013U
#define LUMA_FOCAL_REPORT_EVENT 0x1018U
#define LUMA_FOCAL_QUERY_EVENT_STATUS 0x101dU
#define LUMA_FOCAL_QUERY_FINGER_STATUS 0x101eU
#define LUMA_FOCAL_CONFIGURE_WORK_MODE 0x1020U

module_param_named(luma_focal_enroll_service, luma_focal_enroll_service,
		   bool, 0400);
MODULE_PARM_DESC(luma_focal_enroll_service,
		 "expose the bounded root-only FP6 enrollment transport");

static DEFINE_MUTEX(luma_enrollment_exchange_lock);
static bool luma_enrollment_device_registered;

static int luma_gatekeeper_direct_exchange(
	struct qcomtee *qcomtee, struct qcomtee_object *controller,
	u32 command, const void *payload, size_t payload_size,
	void *response_payload, size_t response_capacity,
	size_t *response_size, s32 *trustlet_status)
{
	/*
	 * UID-151/selector-150 returns Keymaster's direct controller.  Its
	 * command ABI is one input buffer plus one output buffer; wrapping that
	 * controller in QSEEComCompat's ten-argument SEND_REQUEST envelope is a
	 * category error and secure world rejects it before parsing the command.
	 */
	return luma_keymaster_generic_exchange(
		qcomtee, controller, command, payload, payload_size,
		response_payload, response_capacity, response_size,
		trustlet_status);
}

static bool luma_gatekeeper_command_allowed(u32 command)
{
	return command == LUMA_GATEKEEPER_ENROLL ||
		command == LUMA_GATEKEEPER_VERIFY ||
		command == LUMA_GATEKEEPER_DELETE_USER ||
		command == LUMA_GATEKEEPER_DELETE_ALL_USERS;
}

static bool luma_focal_command_allowed(u32 command)
{
	switch (command) {
	case LUMA_FOCAL_PRE_ENROLL:
	case LUMA_FOCAL_ENROLL:
	case LUMA_FOCAL_POST_ENROLL:
	case LUMA_FOCAL_CANCEL:
	case LUMA_FOCAL_ENUMERATE_ALLOWED:
	case LUMA_FOCAL_SET_ACTIVE_GROUP:
	case LUMA_FOCAL_SYNC_STATISTICS:
	case LUMA_FOCAL_SYNC_TEMPLATE:
	case LUMA_FOCAL_CAPTURE_IMAGE:
	case LUMA_FOCAL_REPORT_EVENT:
	case LUMA_FOCAL_QUERY_EVENT_STATUS:
	case LUMA_FOCAL_QUERY_FINGER_STATUS:
	case LUMA_FOCAL_CONFIGURE_WORK_MODE:
		return true;
	default:
		return false;
	}
}

static bool luma_focal_shape_allowed(
	const struct luma_fp6_secure_exchange *exchange)
{
	u32 previous_hardware_process = 0;
	u32 capture_control_flags = 0;

	switch (exchange->command) {
	case LUMA_FOCAL_PRE_ENROLL:
		return !exchange->request_size &&
			exchange->response_capacity == sizeof(u64);
	case LUMA_FOCAL_ENROLL:
		return exchange->request_size == 74U &&
			exchange->response_capacity == sizeof(u32);
	case LUMA_FOCAL_POST_ENROLL:
	case LUMA_FOCAL_CANCEL:
		return !exchange->request_size && !exchange->response_capacity;
	case LUMA_FOCAL_ENUMERATE_ALLOWED:
		return exchange->request_size == sizeof(u32) &&
			exchange->response_capacity <= 260U;
	case LUMA_FOCAL_SET_ACTIVE_GROUP: {
		u32 group = ~0U;
		static const char path[] = LUMA_FOCAL_ACTIVE_GROUP_PATH;

		if (exchange->request_size != sizeof(group) + sizeof(path) ||
		    exchange->response_capacity)
			return false;
		memcpy(&group, exchange->payload, sizeof(group));
		return group == 0U &&
			!memcmp(exchange->payload + sizeof(group), path,
				sizeof(path));
	}
	case LUMA_FOCAL_SYNC_STATISTICS: {
		static const u16 sentinel_offsets[] = {
			0xa8, 0xbc, 0xd0, 0xe4, 0xf8, 0x10c, 0x120, 0x134,
			0x148, 0x15c, 0x170, 0x184, 0x198, 0x1ac, 0x1c0,
			0x1d4, 0x1e8, 0x218,
		};
		u8 expected[LUMA_FOCAL_STATISTICS_SIZE] = {};
		s32 unset = -1;
		size_t i;
		bool allowed;

		if (exchange->request_size != sizeof(expected) ||
		    exchange->response_capacity)
			return false;
		for (i = 0; i < ARRAY_SIZE(sentinel_offsets); i++)
			memcpy(expected + sentinel_offsets[i], &unset,
			       sizeof(unset));
		allowed = !memcmp(exchange->payload, expected, sizeof(expected));
		memzero_explicit(expected, sizeof(expected));
		return allowed;
	}
	case LUMA_FOCAL_SYNC_TEMPLATE: {
		u32 group = ~0U;
		u32 finger = 0U;
		u32 data_size = 0U;

		if (exchange->response_capacity != 16312U ||
		    exchange->request_size < 2U * sizeof(u32))
			return false;
		memcpy(&group, exchange->payload, sizeof(group));
		memcpy(&finger, exchange->payload + sizeof(group),
		       sizeof(finger));
		if (group != 0U || finger == 0U)
			return false;
		if (exchange->request_size == 2U * sizeof(u32))
			return true;
		if (exchange->request_size <
		    2U * sizeof(u32) + 16U)
			return false;
		memcpy(&data_size,
		       exchange->payload + 2U * sizeof(u32) + 12U,
		       sizeof(data_size));
		return exchange->request_size ==
			2U * sizeof(u32) + 16U + (size_t)data_size;
	}
	case LUMA_FOCAL_CAPTURE_IMAGE:
		if (exchange->request_size != 32U ||
		    exchange->response_capacity != 32U)
			return false;
		/*
		 * Word 0 is one only for the first capture after stock
		 * ff_device_hw_reset(false), then zero for the remainder of the burst.
		 * It does not request or return a normal-world image.
		 * Word 24 is the exact QREL enrollment algorithm/device-mode value.
		 */
		memcpy(&previous_hardware_process, exchange->payload,
		       sizeof(previous_hardware_process));
		memcpy(&capture_control_flags, exchange->payload + 24,
		       sizeof(capture_control_flags));
		return previous_hardware_process <= 1U &&
			capture_control_flags == 0xc0040002U;
	case LUMA_FOCAL_REPORT_EVENT: {
		u32 event_type = 0;
		u32 event_flags = 0;
		u32 reserved_2cc = ~0U;
		u32 reserved_2d8 = ~0U;

		if (exchange->request_size != 732U ||
		    exchange->response_capacity != 732U)
			return false;
		memcpy(&event_type, exchange->payload + 4, sizeof(event_type));
		if (event_type == 6U) {
			u8 expected[732] = {};

			memcpy(expected + 4, &event_type, sizeof(event_type));
			return !memcmp(exchange->payload, expected, sizeof(expected));
		}
		if (event_type != 5U && event_type != 7U)
			return false;
		memcpy(&reserved_2cc, exchange->payload + 0x2cc,
		       sizeof(reserved_2cc));
		memcpy(&event_flags, exchange->payload + 0x2d4,
		       sizeof(event_flags));
		memcpy(&reserved_2d8, exchange->payload + 0x2d8,
		       sizeof(reserved_2d8));
		return reserved_2cc == 0U && reserved_2d8 == 0U &&
			event_flags == 0x08080004U;
	}
	case LUMA_FOCAL_QUERY_EVENT_STATUS:
	case LUMA_FOCAL_QUERY_FINGER_STATUS:
		return exchange->request_size == sizeof(u32) &&
			!exchange->response_capacity;
	case LUMA_FOCAL_CONFIGURE_WORK_MODE: {
		u32 mode = 0;

		if (exchange->request_size != sizeof(mode) ||
		    exchange->response_capacity)
			return false;
		memcpy(&mode, exchange->payload, sizeof(mode));
		return mode == 1U || mode == 2U;
	}
	default:
		return false;
	}
}

/*
 * DELETE_ALL_USERS commits the new protected Gatekeeper state and retires the
 * authenticated Gatekeeper service chain that performed the reset. Reusing
 * either its direct command object or its app client makes the next selector
 * request fail with QCOMTEE_MSG_ERROR_UNAVAIL even though the reset itself
 * succeeded. Release that complete chain and reacquire it through a fresh
 * Android credential environment, matching a new stock Gatekeeper service
 * instance. Focal key synchronization deliberately remains untouched.
 */
static int luma_gatekeeper_refresh_session(
	struct qcomtee *qcomtee, struct luma_gatekeeper_session *session)
{
	int ret;

	if (!qcomtee || !session)
		return -ENODEV;
	luma_gatekeeper_release(qcomtee, session);
	ret = luma_keymaster_prepare_service(qcomtee, session);
	if (ret) {
		pr_err("luma-keymaster: post-reset Gatekeeper session reacquire failed=%d\n",
		       ret);
		return ret;
	}
	pr_info("luma-keymaster: post-reset Gatekeeper session refreshed\n");
	return 0;
}

/*
 * DELETE_USER commits the per-user table and retires only the direct command
 * object.  The authenticated service and app client remain valid on the
 * fresh boot after DELETE_ALL_USERS.  Reacquire selector 150 through that
 * retained app client before ENROLL, matching a new stock Gatekeeper request
 * object without repeating global KeyMint startup or Focal key sync.
 */
static int luma_gatekeeper_refresh_controller(
	struct qcomtee *qcomtee, struct luma_gatekeeper_session *session)
{
	struct qcomtee_arg args[3] = { 0 };
	u32 selector = LUMA_KEYMASTER_SELECTOR;
	int ret, result = QCOMTEE_MSG_ERROR_UNAVAIL;

	if (!qcomtee || !session ||
	    session->app_client == NULL_QCOMTEE_OBJECT)
		return -ENODEV;
	luma_control_put_sync(qcomtee, &session->controller);
	args[0].b.addr = &selector;
	args[0].b.size = sizeof(selector);
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	args[1].type = QCOMTEE_ARG_TYPE_OO;
	ret = luma_control_invoke(qcomtee, session->app_client,
				  LUMA_KEYMASTER_INVOKE, args, &result);
	if (ret || result) {
		pr_err("luma-keymaster: post-delete-user selector=%u transport=%d result=%d\n",
		       selector, ret, result);
		return ret ?: -EREMOTEIO;
	}
	session->controller = args[1].o;
	ret = luma_keymaster_prepare_client(qcomtee, session->controller, false);
	if (ret) {
		luma_control_put_sync(qcomtee, &session->controller);
		return ret;
	}
	pr_info("luma-keymaster: post-delete-user Gatekeeper controller refreshed\n");
	return 0;
}

static long luma_enrollment_exchange(
	struct luma_fp6_secure_exchange *exchange)
{
	struct luma_focal_session *focal = &luma_focal_session;
	size_t response_size = 0;
	s32 secure_status = 0;
	int ret;

	if (exchange->abi_version != LUMA_FP6_ENROLLMENT_ABI_VERSION ||
	    exchange->reserved ||
	    exchange->request_size > LUMA_FP6_ENROLLMENT_MAX_PAYLOAD ||
	    exchange->response_capacity > LUMA_FP6_ENROLLMENT_MAX_PAYLOAD)
		return -EINVAL;
	exchange->response_size = 0;
	exchange->secure_status = 0;
	mutex_lock(&luma_focal_session_lock);
	if (focal->stage != LUMA_FOCAL_STAGE_COMPLETE || !focal->qcomtee ||
	    focal->controller == NULL_QCOMTEE_OBJECT) {
		ret = -ENODEV;
		goto out_unlock;
	}
	if (exchange->service == LUMA_FP6_SERVICE_GATEKEEPER) {
		if (!luma_gatekeeper_command_allowed(exchange->command)) {
			ret = -EPERM;
			goto out_unlock;
		}
		if (focal->gatekeeper.controller == NULL_QCOMTEE_OBJECT) {
			ret = -ENODEV;
			goto out_unlock;
		}
		ret = luma_gatekeeper_direct_exchange(
			focal->qcomtee, focal->gatekeeper.controller,
			exchange->command,
			exchange->payload, exchange->request_size,
			exchange->payload, exchange->response_capacity,
			&response_size, &secure_status);
		if (!ret &&
		    exchange->command == LUMA_GATEKEEPER_DELETE_ALL_USERS &&
		    secure_status == 0 && response_size == 0)
			ret = luma_gatekeeper_refresh_session(
				focal->qcomtee, &focal->gatekeeper);
		else if (!ret &&
			 exchange->command == LUMA_GATEKEEPER_DELETE_USER &&
			 (secure_status == 0 || secure_status == -1) &&
			 response_size == 0)
			ret = luma_gatekeeper_refresh_controller(
				focal->qcomtee, &focal->gatekeeper);
	} else if (exchange->service == LUMA_FP6_SERVICE_FOCAL) {
		if (!luma_focal_command_allowed(exchange->command) ||
		    !luma_focal_shape_allowed(exchange)) {
			ret = -EPERM;
			goto out_unlock;
		}
		/*
		 * Stock focal64 uses one persistent QSEECom shared buffer for the full
		 * five-pass CAPTURE_IMAGE / QUERY_FINGER_STATUS burst and its final
		 * REPORT_EVENT. Allocate it at the first capture, reuse it across the
		 * bounded burst, and wipe it at report or on every unrelated path. The
		 * opaque bulk acquisition state remains kernel-only throughout.
		 */
		if (exchange->command == LUMA_FOCAL_CAPTURE_IMAGE) {
			if (!focal->capture_buffer_pending) {
				focal->capture_send_buffer =
					vzalloc(LUMA_FOCAL_SEND_SIZE);
				focal->capture_response_buffer =
					vzalloc(LUMA_FOCAL_RESPONSE_SIZE);
				focal->capture_oic = qcomtee_object_invoke_ctx_alloc(
					focal->qcomtee->ctx);
				if (!focal->capture_send_buffer ||
				    !focal->capture_response_buffer ||
				    !focal->capture_oic) {
					luma_focal_capture_buffers_clear(focal);
					ret = -ENOMEM;
					goto out_unlock;
				}
				focal->capture_oic->flags |=
					QCOMTEE_OIC_FLAG_RETAIN_SHM;
				focal->capture_buffer_pending = true;
			}
		} else if (exchange->command ==
			   LUMA_FOCAL_QUERY_FINGER_STATUS &&
			   focal->capture_buffer_pending) {
			/* This query is the stock boundary between burst passes. */
		} else if (exchange->command == LUMA_FOCAL_REPORT_EVENT) {
			/*
			 * A type-6 REPORT_EVENT is the stock finger-release message. It
			 * legitimately follows the IRQ edge after the completed capture /
			 * type-5-or-7 report pair, so there is no retained acquisition
			 * buffer at that point. Other report types still require the
			 * immediately preceding bounded capture.
			 */
			if (!focal->capture_buffer_pending) {
				u32 event_type;

				memcpy(&event_type, exchange->payload + 4,
				       sizeof(event_type));
				if (event_type != 6U) {
					ret = -EPERM;
					goto out_unlock;
				}
			}
		} else {
			luma_focal_capture_buffers_clear(focal);
		}
		ret = luma_focal_exchange_status(
			focal->qcomtee, focal->controller, exchange->command,
			exchange->payload, exchange->request_size,
			exchange->payload, exchange->response_capacity,
			&response_size, &secure_status,
			focal->capture_buffer_pending ?
				focal->capture_send_buffer : NULL,
			focal->capture_buffer_pending ?
				focal->capture_response_buffer : NULL,
			focal->capture_buffer_pending ?
				focal->capture_oic : NULL);
		if (exchange->command == LUMA_FOCAL_REPORT_EVENT ||
		    (exchange->command == LUMA_FOCAL_CAPTURE_IMAGE && ret))
			luma_focal_capture_buffers_clear(focal);
		/*
		 * Query, capture, and event commands intentionally use the TA
		 * status word for state-machine results such as retry/acquired.
		 * Preserve that word for the audited userspace state machine.
		 */
		if (ret == -EREMOTEIO &&
		    (exchange->command == LUMA_FOCAL_QUERY_EVENT_STATUS ||
		     exchange->command == LUMA_FOCAL_QUERY_FINGER_STATUS ||
		     exchange->command == LUMA_FOCAL_CAPTURE_IMAGE ||
		     exchange->command == LUMA_FOCAL_REPORT_EVENT))
			ret = 0;
		/*
		 * QREL 16.95.0 intentionally ignores the fresh-store -2 returned by
		 * SET_ACTIVE_GROUP and immediately synchronizes its reset statistics.
		 * Preserve only that exact status for the userspace parity check; no
		 * other setup-command rejection is downgraded.
		 */
		if (ret == -EREMOTEIO &&
		    exchange->command == LUMA_FOCAL_SET_ACTIVE_GROUP &&
		    secure_status == -2 && response_size == 0)
			ret = 0;
	} else {
		ret = -EINVAL;
		goto out_unlock;
	}
	if (!ret) {
		exchange->response_size = response_size;
		exchange->secure_status = secure_status;
	}
out_unlock:
	mutex_unlock(&luma_focal_session_lock);
	return ret;
}

static long luma_enrollment_ioctl(struct file *file, unsigned int command,
				  unsigned long argument)
{
	struct luma_fp6_secure_exchange *exchange;
	long ret;

	(void)file;
	if (!capable(CAP_SYS_ADMIN))
		return -EPERM;
	if (command != LUMA_FP6_ENROLLMENT_IOC_EXCHANGE)
		return -ENOTTY;
	exchange = kvzalloc(sizeof(*exchange), GFP_KERNEL);
	if (!exchange)
		return -ENOMEM;
	if (copy_from_user(exchange, (void __user *)argument,
			   sizeof(*exchange))) {
		ret = -EFAULT;
		goto out;
	}
	mutex_lock(&luma_enrollment_exchange_lock);
	ret = luma_enrollment_exchange(exchange);
	mutex_unlock(&luma_enrollment_exchange_lock);
	if (!ret && copy_to_user((void __user *)argument, exchange,
				 sizeof(*exchange)))
		ret = -EFAULT;
out:
	memzero_explicit(exchange, sizeof(*exchange));
	kvfree(exchange);
	return ret;
}

static const struct file_operations luma_enrollment_fops = {
	.owner = THIS_MODULE,
	.unlocked_ioctl = luma_enrollment_ioctl,
#ifdef CONFIG_COMPAT
	.compat_ioctl = luma_enrollment_ioctl,
#endif
	.llseek = noop_llseek,
};

static struct miscdevice luma_enrollment_miscdev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "luma-fp6-fingerprint",
	.fops = &luma_enrollment_fops,
	.mode = 0600,
};

static int luma_enrollment_register(void)
{
	int ret;

	if (luma_enrollment_device_registered)
		return -EALREADY;
	ret = misc_register(&luma_enrollment_miscdev);
	if (!ret) {
		luma_enrollment_device_registered = true;
		pr_info("luma-focal: root-only enrollment transport registered payload_contents_logged=0 raw_images=0\n");
	}
	return ret;
}

static void luma_enrollment_unregister(void)
{
	if (!luma_enrollment_device_registered)
		return;
	misc_deregister(&luma_enrollment_miscdev);
	luma_enrollment_device_registered = false;
}
