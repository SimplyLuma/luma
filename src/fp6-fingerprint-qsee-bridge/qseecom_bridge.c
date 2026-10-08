/* SPDX-License-Identifier: Apache-2.0 */

#include "QSEEComAPI.h"
#include "qseecom_bridge_internal.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/tee.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#define LUMA_QSEE_IMPL_ID 5u
#define LUMA_QSEE_APP_NAME_MAX 64u
#define LUMA_QSEE_BUFFER_MAX (4u * 1024u * 1024u)
#define LUMA_QSEE_HANDLE_MAGIC UINT64_C(0x4c554d4151534545)

struct luma_qsee_shm {
	int id;
	void *address;
	size_t size;
};

struct luma_qsee_handle {
	/* This must remain first: it is the published QSEECom ABI. */
	struct QSEECom_handle public;
	uint64_t magic;
	int tee_fd;
	uint32_t session;
	struct luma_qsee_shm shared;
	pthread_mutex_t command_lock;
};

static int real_open_device(const char *path, int flags)
{
	return open(path, flags);
}

static int real_close_fd(int fd)
{
	return close(fd);
}

static int real_ioctl_call(int fd, unsigned long request, void *argument)
{
#ifdef __ANDROID__
	return ioctl(fd, (unsigned int)request, argument);
#else
	return ioctl(fd, request, argument);
#endif
}

static void *real_map_memory(size_t length, int fd)
{
	return mmap(NULL, length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
}

static int real_unmap_memory(void *address, size_t length)
{
	return munmap(address, length);
}

static const struct luma_qsee_ops real_ops = {
	.open_device = real_open_device,
	.close_fd = real_close_fd,
	.ioctl_call = real_ioctl_call,
	.map_memory = real_map_memory,
	.unmap_memory = real_unmap_memory,
};

static const struct luma_qsee_ops *active_ops = &real_ops;

#ifdef LUMA_QSEE_TEST
void luma_qsee_set_test_ops(const struct luma_qsee_ops *ops)
{
	active_ops = ops ? ops : &real_ops;
}
#endif

static int fail(int error)
{
	errno = error;
	return -1;
}

static int alloc_shm(const struct luma_qsee_ops *ops, int tee_fd, size_t size,
		     struct luma_qsee_shm *memory)
{
	struct tee_ioctl_shm_alloc_data allocation = { .size = size };
	void *address;
	int shm_fd;
	int saved_errno;

	if (!size || size > LUMA_QSEE_BUFFER_MAX)
		return fail(EINVAL);

	shm_fd = ops->ioctl_call(tee_fd, TEE_IOC_SHM_ALLOC, &allocation);
	if (shm_fd < 0)
		return -1;
	if (allocation.id < 0 || allocation.size < size ||
	    allocation.size > LUMA_QSEE_BUFFER_MAX) {
		ops->close_fd(shm_fd);
		return fail(EOVERFLOW);
	}

	address = ops->map_memory(allocation.size, shm_fd);
	saved_errno = errno;
	ops->close_fd(shm_fd);
	if (address == MAP_FAILED) {
		errno = saved_errno;
		return -1;
	}

	memset(address, 0, allocation.size);
	memory->id = allocation.id;
	memory->address = address;
	memory->size = allocation.size;
	return 0;
}

static void free_shm(const struct luma_qsee_ops *ops,
		     struct luma_qsee_shm *memory)
{
	if (memory->address && memory->address != MAP_FAILED)
		ops->unmap_memory(memory->address, memory->size);
	memory->id = 0;
	memory->address = NULL;
	memory->size = 0;
}

static int open_qseecom_device(const struct luma_qsee_ops *ops,
			       unsigned int *next_index)
{
	struct tee_ioctl_version_data version;
	char path[32];
	int fd;

	if (!next_index || *next_index >= 8)
		return fail(ENODEV);
	for (unsigned int index = *next_index; index < 8; index++) {
		*next_index = index + 1;
		if (snprintf(path, sizeof(path), "/dev/tee%u", index) >=
		    (int)sizeof(path))
			return fail(EOVERFLOW);
		fd = ops->open_device(path, O_RDWR | O_CLOEXEC);
		if (fd < 0)
			continue;
		memset(&version, 0, sizeof(version));
		if (!ops->ioctl_call(fd, TEE_IOC_VERSION, &version) &&
		    version.impl_id == LUMA_QSEE_IMPL_ID)
			return fd;
		ops->close_fd(fd);
	}
	return fail(ENODEV);
}

static int open_named_session(const struct luma_qsee_ops *ops, int tee_fd,
			      const char *application, uint32_t *session_id)
{
	uint64_t request_storage[(sizeof(struct tee_ioctl_open_session_arg) +
				  sizeof(struct tee_ioctl_param) + 7) / 8] = {};
	struct tee_ioctl_open_session_arg *request = (void *)request_storage;
	struct tee_ioctl_param *params = request->params;
	struct tee_ioctl_buf_data data;
	struct luma_qsee_shm name = {};
	size_t length = strlen(application) + 1;
	int result;

	if (alloc_shm(ops, tee_fd, length, &name))
		return -1;
	memcpy(name.address, application, length);

	request->num_params = 1;
	params[0].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INPUT;
	params[0].b = length;
	params[0].c = (uint32_t)name.id;
	data.buf_ptr = (uintptr_t)request_storage;
	data.buf_len = sizeof(request_storage);
	result = ops->ioctl_call(tee_fd, TEE_IOC_OPEN_SESSION, &data);
	free_shm(ops, &name);
	if (result)
		return -1;
	if (request->ret)
		return fail(EPROTO);

	*session_id = request->session;
	return 0;
}

static bool valid_application_name(const char *name)
{
	size_t length;

	if (!name)
		return false;
	length = strnlen(name, LUMA_QSEE_APP_NAME_MAX);
	return length > 0 && length < LUMA_QSEE_APP_NAME_MAX &&
	       !strchr(name, '/');
}

int QSEECom_start_app(struct QSEECom_handle **clnt_handle, const char *path,
		      const char *fname, uint32_t sb_size)
{
	const struct luma_qsee_ops *ops = active_ops;
	struct luma_qsee_handle *handle;
	unsigned int next_index = 0;
	int session_errno = 0;
	int saved_errno;

	/* Loading is intentionally delegated to the separately confined loader. */
	(void)path;
	if (!clnt_handle || *clnt_handle || !valid_application_name(fname) ||
	    !sb_size || sb_size > LUMA_QSEE_BUFFER_MAX)
		return fail(EINVAL);

	handle = calloc(1, sizeof(*handle));
	if (!handle)
		return -1;
	handle->tee_fd = -1;

	/*
	 * Multiple QSEE-capable providers can coexist during the FP6 migration:
	 * qcomtee owns the already-loaded signed TA while qseecomtee owns the
	 * stock listener-capable client device.  Try each matching TEE provider
	 * until one accepts the named application instead of silently selecting
	 * the first implementation-ID match.
	 */
	for (;;) {
		handle->tee_fd = open_qseecom_device(ops, &next_index);
		if (handle->tee_fd < 0) {
			if (session_errno)
				errno = session_errno;
			goto error;
		}
		if (!open_named_session(ops, handle->tee_fd, fname,
					&handle->session))
			break;
		session_errno = errno;
		ops->close_fd(handle->tee_fd);
		handle->tee_fd = -1;
	}
	if (alloc_shm(ops, handle->tee_fd, sb_size, &handle->shared))
		goto error;
	if (pthread_mutex_init(&handle->command_lock, NULL)) {
		errno = EBUSY;
		goto error;
	}

	handle->public.ion_sbuffer = handle->shared.address;
	handle->magic = LUMA_QSEE_HANDLE_MAGIC;
	*clnt_handle = &handle->public;
	return 0;

error:
	saved_errno = errno;
	free_shm(ops, &handle->shared);
	if (handle->tee_fd >= 0)
		ops->close_fd(handle->tee_fd);
	free(handle);
	errno = saved_errno;
	return -1;
}

int QSEECom_shutdown_app(struct QSEECom_handle **public_handle)
{
	const struct luma_qsee_ops *ops = active_ops;
	struct luma_qsee_handle *handle;
	int close_result;
	int saved_errno;

	if (!public_handle || !*public_handle)
		return fail(EINVAL);
	handle = (struct luma_qsee_handle *)*public_handle;
	if (handle->magic != LUMA_QSEE_HANDLE_MAGIC)
		return fail(EINVAL);

	handle->magic = 0;
	*public_handle = NULL;
	free_shm(ops, &handle->shared);
	close_result = ops->close_fd(handle->tee_fd);
	saved_errno = errno;
	pthread_mutex_destroy(&handle->command_lock);
	free(handle);
	if (close_result) {
		errno = saved_errno;
		return -1;
	}
	return 0;
}

int QSEECom_send_cmd(struct QSEECom_handle *public_handle, void *send_buf,
		     uint32_t sbuf_len, void *rcv_buf, uint32_t rbuf_len)
{
	const struct luma_qsee_ops *ops = active_ops;
	uint64_t invoke_storage[(sizeof(struct tee_ioctl_invoke_arg) +
				2 * sizeof(struct tee_ioctl_param) + 7) / 8] = {};
	struct tee_ioctl_invoke_arg *invoke = (void *)invoke_storage;
	struct tee_ioctl_param *params = invoke->params;
	struct tee_ioctl_buf_data data;
	struct luma_qsee_handle *handle = (void *)public_handle;
	struct luma_qsee_shm request = {};
	struct luma_qsee_shm response = {};
	int result = -1;
	int saved_errno;

	if (!handle || handle->magic != LUMA_QSEE_HANDLE_MAGIC || !send_buf ||
	    !rcv_buf || !sbuf_len || !rbuf_len ||
	    sbuf_len > LUMA_QSEE_BUFFER_MAX || rbuf_len > LUMA_QSEE_BUFFER_MAX)
		return fail(EINVAL);

	if (pthread_mutex_lock(&handle->command_lock))
		return fail(EBUSY);
	if (alloc_shm(ops, handle->tee_fd, sbuf_len, &request))
		goto out;
	if (alloc_shm(ops, handle->tee_fd, rbuf_len, &response))
		goto out;
	memcpy(request.address, send_buf, sbuf_len);

	invoke->func = 0;
	invoke->session = handle->session;
	invoke->num_params = 2;
	params[0].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INPUT;
	params[0].b = sbuf_len;
	params[0].c = (uint32_t)request.id;
	params[1].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_OUTPUT;
	params[1].b = rbuf_len;
	params[1].c = (uint32_t)response.id;
	data.buf_ptr = (uintptr_t)invoke_storage;
	data.buf_len = sizeof(invoke_storage);
	if (ops->ioctl_call(handle->tee_fd, TEE_IOC_INVOKE, &data))
		goto out;
	if (invoke->ret) {
		errno = EPROTO;
		goto out;
	}

	memcpy(rcv_buf, response.address, rbuf_len);
	result = 0;

out:
	saved_errno = errno;
	free_shm(ops, &response);
	free_shm(ops, &request);
	pthread_mutex_unlock(&handle->command_lock);
	errno = saved_errno;
	return result;
}
