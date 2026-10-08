/* SPDX-License-Identifier: GPL-2.0-only */

/*
 * Stock-compatible QSEEComCompat listener callbacks for the FP6.  Secure
 * world writes each request into the listener's shared memory object, invokes
 * the callback object, and waits while a root-only normal-world daemon
 * services that buffer.  No request contents are logged by this transport.
 */

#include <linux/fs.h>
#include <linux/freezer.h>
#include <linux/miscdevice.h>
#include <linux/tee_drv.h>
#include <linux/uaccess.h>
#include <linux/wait.h>

#include "luma-fp6-listener-abi.h"

#define LUMA_QSEECOMCOMPAT_REGISTER_LISTENER 0U
#define LUMA_QSEECOMCOMPAT_LISTENER_SERVICE_UID 87U
#define LUMA_LISTENER_FS_ID 10U
#define LUMA_LISTENER_RPMB_ID 8192U
#define LUMA_LISTENER_GPFS_ID 28672U
#define LUMA_LISTENER_FS_SIZE (20U * 1024U)
#define LUMA_LISTENER_RPMB_SIZE 20040U
#define LUMA_LISTENER_GPFS_SIZE (504U * 1024U)
#define LUMA_LISTENER_CALLBACK_TIMEOUT (30U * HZ)

struct luma_qsee_listener {
	struct qcomtee_object callback;
	struct qcomtee_object *memory_object;
	struct tee_shm *shm;
	void *buffer;
	size_t size;
	u32 id;
	struct mutex lock;
	wait_queue_head_t response_wait;
	u64 sequence;
	bool callback_initialized;
	bool registered;
	bool pending;
	bool delivered;
	bool responded;
	bool stopping;
	s32 dispatch_status;
};

static struct luma_qsee_listener luma_qsee_listeners[] = {
	{ .id = LUMA_LISTENER_FS_ID, .size = LUMA_LISTENER_FS_SIZE },
	{ .id = LUMA_LISTENER_RPMB_ID, .size = LUMA_LISTENER_RPMB_SIZE },
	{ .id = LUMA_LISTENER_GPFS_ID, .size = LUMA_LISTENER_GPFS_SIZE },
};

static DECLARE_WAIT_QUEUE_HEAD(luma_listener_request_wait);
static DEFINE_MUTEX(luma_listener_ioctl_lock);
static bool luma_listener_device_registered;
static bool luma_listeners_stopping;
static struct qcomtee_object *luma_listener_service = NULL_QCOMTEE_OBJECT;

static void luma_listener_callback_release(struct qcomtee_object *object)
{
	/* Storage is owned by the static listener entry and released explicitly. */
	(void)object;
}

static int luma_listener_callback_dispatch(
	struct qcomtee_object_invoke_ctx *oic,
	struct qcomtee_object *object, u32 op, struct qcomtee_arg *args)
{
	struct luma_qsee_listener *listener = container_of(
		object, struct luma_qsee_listener, callback);
	long waited;
	int i;

	(void)oic;
	if (op != 0U || args[0].type != QCOMTEE_ARG_TYPE_OB ||
	    args[0].b.size != 4U * sizeof(u32) ||
	    args[1].type != QCOMTEE_ARG_TYPE_OB ||
	    args[1].b.size != sizeof(s32)) {
		pr_err("luma-listener: callback shape rejected id=%u op=%u type0=%u size0=%zu contents_logged=0\n",
		       listener->id, op, args[0].type,
		       args[0].type == QCOMTEE_ARG_TYPE_OB ? args[0].b.size : 0);
		return -EINVAL;
	}
	for (i = 2; i <= 5; i++)
		if (args[i].type != QCOMTEE_ARG_TYPE_OO) {
			pr_err("luma-listener: callback object shape rejected id=%u op=%u index=%d type=%u contents_logged=0\n",
			       listener->id, op, i, args[i].type);
			return -EINVAL;
		}

	mutex_lock(&listener->lock);
	if (listener->pending || listener->stopping) {
		mutex_unlock(&listener->lock);
		return -EBUSY;
	}
	listener->sequence++;
	listener->pending = true;
	listener->delivered = false;
	listener->responded = false;
	listener->dispatch_status = 0;
	mutex_unlock(&listener->lock);
	wake_up_all(&luma_listener_request_wait);

	waited = wait_event_killable_timeout(
		listener->response_wait,
		READ_ONCE(listener->responded) || READ_ONCE(listener->stopping),
		LUMA_LISTENER_CALLBACK_TIMEOUT);
	mutex_lock(&listener->lock);
	if (waited <= 0 || listener->stopping || !listener->responded) {
		listener->pending = false;
		listener->delivered = false;
		mutex_unlock(&listener->lock);
		return waited == 0 ? -ETIMEDOUT : -EINTR;
	}
	memset(args[0].b.addr, 0, args[0].b.size);
	memcpy(args[1].b.addr, &listener->dispatch_status,
	       sizeof(listener->dispatch_status));
	for (i = 2; i <= 5; i++)
		args[i].o = NULL_QCOMTEE_OBJECT;
	listener->pending = false;
	listener->delivered = false;
	listener->responded = false;
	mutex_unlock(&listener->lock);
	return 0;
}

static struct qcomtee_object_operations luma_listener_callback_ops = {
	.release = luma_listener_callback_release,
	.dispatch = luma_listener_callback_dispatch,
};

static struct luma_qsee_listener *luma_listener_find_pending(void)
{
	int i;

	for (i = 0; i < ARRAY_SIZE(luma_qsee_listeners); i++) {
		struct luma_qsee_listener *listener = &luma_qsee_listeners[i];

		mutex_lock(&listener->lock);
		if (listener->pending && !listener->delivered &&
		    !listener->stopping) {
			listener->delivered = true;
			mutex_unlock(&listener->lock);
			return listener;
		}
		mutex_unlock(&listener->lock);
	}
	return NULL;
}

static bool luma_listener_has_pending(void)
{
	int i;

	for (i = 0; i < ARRAY_SIZE(luma_qsee_listeners); i++)
		if (READ_ONCE(luma_qsee_listeners[i].pending) &&
		    !READ_ONCE(luma_qsee_listeners[i].delivered))
			return true;
	return false;
}

static bool luma_listener_wait_ready(void)
{
	return READ_ONCE(luma_listeners_stopping) ||
	       luma_listener_has_pending();
}

static struct luma_qsee_listener *luma_listener_by_id(u32 id)
{
	int i;

	for (i = 0; i < ARRAY_SIZE(luma_qsee_listeners); i++)
		if (luma_qsee_listeners[i].id == id)
			return &luma_qsee_listeners[i];
	return NULL;
}

static long luma_listener_ioctl(struct file *file, unsigned int command,
				unsigned long argument)
{
	struct luma_fp6_listener_message *message;
	struct luma_qsee_listener *listener;
	long ret = 0;

	(void)file;
	if (!capable(CAP_SYS_ADMIN))
		return -EPERM;
	if (command != LUMA_FP6_LISTENER_IOC_WAIT &&
	    command != LUMA_FP6_LISTENER_IOC_RESPOND)
		return -ENOTTY;
	message = kzalloc(sizeof(*message), GFP_KERNEL);
	if (!message)
		return -ENOMEM;

	mutex_lock(&luma_listener_ioctl_lock);
	if (command == LUMA_FP6_LISTENER_IOC_WAIT) {
		if (copy_from_user(message, (void __user *)argument,
				   sizeof(*message))) {
			ret = -EFAULT;
			goto out;
		}
		if (message->abi_version != LUMA_FP6_LISTENER_ABI_VERSION ||
		    message->reserved || !message->payload_ptr) {
			ret = -EINVAL;
			goto out;
		}
		/*
		 * This ioctl is intentionally long-lived while the root listener
		 * daemon waits for secure-world work. A merely killable wait leaves
		 * the userspace task outside the freezer and can block system suspend
		 * indefinitely. Freeze here, then resume waiting after thaw.
		 */
		ret = wait_event_freezable(luma_listener_request_wait,
					   luma_listener_wait_ready());
		if (ret)
			goto out;
		if (READ_ONCE(luma_listeners_stopping)) {
			ret = -ESHUTDOWN;
			goto out;
		}
		listener = luma_listener_find_pending();
		if (!listener) {
			ret = -EAGAIN;
			goto out;
		}
		mutex_lock(&listener->lock);
		message->abi_version = LUMA_FP6_LISTENER_ABI_VERSION;
		message->listener_id = listener->id;
		message->sequence = listener->sequence;
		message->buffer_size = listener->size;
		if (message->payload_capacity < listener->size ||
		    copy_to_user(u64_to_user_ptr(message->payload_ptr),
				 listener->buffer, listener->size))
			ret = -EFAULT;
		mutex_unlock(&listener->lock);
		if (ret) {
			mutex_lock(&listener->lock);
			listener->delivered = false;
			mutex_unlock(&listener->lock);
			goto out;
		}
		if (!ret && copy_to_user((void __user *)argument, message,
				 sizeof(*message))) {
			mutex_lock(&listener->lock);
			listener->delivered = false;
			mutex_unlock(&listener->lock);
			ret = -EFAULT;
		}
	} else {
		if (copy_from_user(message, (void __user *)argument,
				   sizeof(*message))) {
			ret = -EFAULT;
			goto out;
		}
		if (message->abi_version != LUMA_FP6_LISTENER_ABI_VERSION ||
		    message->reserved || !message->payload_ptr) {
			ret = -EINVAL;
			goto out;
		}
		listener = luma_listener_by_id(message->listener_id);
		if (!listener || message->buffer_size != listener->size ||
		    message->payload_capacity < listener->size) {
			ret = -EINVAL;
			goto out;
		}
		mutex_lock(&listener->lock);
		if (!listener->pending || !listener->delivered ||
		    listener->sequence != message->sequence ||
		    listener->stopping) {
			ret = -ESTALE;
		} else {
			if (copy_from_user(listener->buffer,
					   u64_to_user_ptr(message->payload_ptr),
					   listener->size)) {
				ret = -EFAULT;
			} else {
				listener->dispatch_status = message->dispatch_status;
				listener->responded = true;
			}
		}
		mutex_unlock(&listener->lock);
		if (!ret)
			wake_up_all(&listener->response_wait);
	}
out:
	mutex_unlock(&luma_listener_ioctl_lock);
	memzero_explicit(message, sizeof(*message));
	kfree(message);
	return ret;
}

static const struct file_operations luma_listener_fops = {
	.owner = THIS_MODULE,
	.unlocked_ioctl = luma_listener_ioctl,
#ifdef CONFIG_COMPAT
	.compat_ioctl = luma_listener_ioctl,
#endif
	.llseek = noop_llseek,
};

static struct miscdevice luma_listener_miscdev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "luma-fp6-qsee-listener",
	.fops = &luma_listener_fops,
	.mode = 0600,
};

static int luma_listener_prepare_one(struct luma_focal_session *session,
				     struct luma_qsee_listener *listener)
{
	struct qcomtee_arg args[4] = { 0 };
	int ret, result = QCOMTEE_MSG_ERROR_UNAVAIL;

	mutex_init(&listener->lock);
	init_waitqueue_head(&listener->response_wait);
	listener->shm = tee_shm_alloc_kernel_buf(session->qcomtee->ctx,
						 listener->size);
	if (IS_ERR(listener->shm)) {
		ret = PTR_ERR(listener->shm);
		listener->shm = NULL;
		return ret;
	}
	listener->buffer = tee_shm_get_va(listener->shm, 0);
	if (IS_ERR(listener->buffer)) {
		ret = PTR_ERR(listener->buffer);
		listener->buffer = NULL;
		goto out_shm;
	}
	memset(listener->buffer, 0, listener->size);
	ret = qcomtee_memobj_shm_to_object(&listener->memory_object,
					   listener->shm);
	if (ret)
		goto out_shm;
	/* The memory object now owns the allocation reference. */
	ret = qcomtee_object_user_init(&listener->callback,
				       QCOMTEE_OBJECT_TYPE_CB,
				       &luma_listener_callback_ops,
				       "luma-listener-%u", listener->id);
	if (ret)
		goto out_memory;
	listener->callback_initialized = true;

	args[0].b.addr = &listener->id;
	args[0].b.size = sizeof(listener->id);
	args[0].type = QCOMTEE_ARG_TYPE_IB;
	if (!qcomtee_object_get(&listener->callback)) {
		ret = -ESTALE;
		goto out_callback;
	}
	args[1].o = &listener->callback;
	args[1].type = QCOMTEE_ARG_TYPE_IO;
	if (!qcomtee_object_get(listener->memory_object)) {
		qcomtee_object_put(&listener->callback);
		ret = -ESTALE;
		goto out_callback;
	}
	args[2].o = listener->memory_object;
	args[2].type = QCOMTEE_ARG_TYPE_IO;
	ret = luma_control_invoke(session->qcomtee, luma_listener_service,
				  LUMA_QSEECOMCOMPAT_REGISTER_LISTENER,
				  args, &result);
	if (ret || result) {
		pr_err("luma-listener: registration rejected id=%u transport=%d result=%d contents_logged=0\n",
		       listener->id, ret, result);
		ret = ret ?: -EREMOTEIO;
		goto out_callback;
	}
	listener->registered = true;
	pr_info("luma-listener: registered id=%u size=%zu transport=qseecomcompat contents_logged=0\n",
		listener->id, listener->size);
	return 0;

out_callback:
	qcomtee_object_put(&listener->callback);
	listener->callback_initialized = false;
out_memory:
	qcomtee_object_put(listener->memory_object);
	listener->memory_object = NULL_QCOMTEE_OBJECT;
	listener->shm = NULL;
	listener->buffer = NULL;
out_shm:
	if (listener->shm)
		tee_shm_put(listener->shm);
	listener->shm = NULL;
	listener->buffer = NULL;
	return ret;
}

static int luma_listener_register_all(struct luma_focal_session *session)
{
	struct qcomtee_object_invoke_ctx *oic;
	int i, ret;

	if (luma_listener_device_registered)
		return -EALREADY;
	WRITE_ONCE(luma_listeners_stopping, false);
	oic = qcomtee_object_invoke_ctx_alloc(session->qcomtee->ctx);
	if (!oic)
		return -ENOMEM;
	luma_listener_service = qcomtee_object_get_service(
		oic, session->client_env,
		LUMA_QSEECOMCOMPAT_LISTENER_SERVICE_UID);
	kfree(oic);
	if (luma_listener_service == NULL_QCOMTEE_OBJECT)
		return -ENODEV;
	pr_info("luma-listener: registration service accepted uid=%u\n",
		LUMA_QSEECOMCOMPAT_LISTENER_SERVICE_UID);
	ret = misc_register(&luma_listener_miscdev);
	if (ret)
		goto out_service;
	luma_listener_device_registered = true;
	for (i = 0; i < ARRAY_SIZE(luma_qsee_listeners); i++) {
		ret = luma_listener_prepare_one(session, &luma_qsee_listeners[i]);
		if (ret)
			return ret;
	}
	return 0;

out_service:
	luma_control_put_sync(session->qcomtee, &luma_listener_service);
	return ret;
}

static void luma_listener_stop_all(void)
{
	int i;

	WRITE_ONCE(luma_listeners_stopping, true);

	for (i = 0; i < ARRAY_SIZE(luma_qsee_listeners); i++) {
		struct luma_qsee_listener *listener = &luma_qsee_listeners[i];

		if (!listener->callback_initialized && !listener->shm)
			continue;
		mutex_lock(&listener->lock);
		listener->stopping = true;
		mutex_unlock(&listener->lock);
		wake_up_all(&listener->response_wait);
	}
	wake_up_all(&luma_listener_request_wait);
	if (luma_listener_device_registered) {
		misc_deregister(&luma_listener_miscdev);
		luma_listener_device_registered = false;
	}
}

static void luma_listener_release_all(struct qcomtee *qcomtee)
{
	int i;

	for (i = ARRAY_SIZE(luma_qsee_listeners) - 1; i >= 0; i--) {
		struct luma_qsee_listener *listener = &luma_qsee_listeners[i];

		if (listener->callback_initialized) {
			qcomtee_object_put(&listener->callback);
			listener->callback_initialized = false;
		}
		if (listener->memory_object != NULL_QCOMTEE_OBJECT) {
			memzero_explicit(listener->buffer, listener->size);
			qcomtee_object_put(listener->memory_object);
			listener->memory_object = NULL_QCOMTEE_OBJECT;
			listener->shm = NULL;
			listener->buffer = NULL;
		}
		listener->registered = false;
		listener->pending = false;
		listener->delivered = false;
		listener->responded = false;
		listener->stopping = false;
	}
	luma_control_put_sync(qcomtee, &luma_listener_service);
}
