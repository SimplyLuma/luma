// SPDX-License-Identifier: Apache-2.0
/*
 * Read-only QSEE application identity probe.
 *
 * This deliberately opens only the ordinary QSEECOM TEE device. It cannot
 * reach the privileged loader endpoint and therefore cannot start an absent
 * trusted application. Closing the file descriptor releases an attached
 * session immediately.
 */

#include <errno.h>
#include <fcntl.h>
#include <linux/tee.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#define TEE_IMPL_ID_QSEECOM 5u
#define APP_NAME_MAX 64u

static int open_qseecom_client(void)
{
	struct tee_ioctl_version_data version;
	char path[32];
	int fd;

	for (unsigned int index = 0; index < 8; index++) {
		if (snprintf(path, sizeof(path), "/dev/tee%u", index) >=
		    (int)sizeof(path))
			return -EOVERFLOW;
		fd = open(path, O_RDWR | O_CLOEXEC);
		if (fd < 0)
			continue;
		memset(&version, 0, sizeof(version));
		if (!ioctl(fd, TEE_IOC_VERSION, &version) &&
		    version.impl_id == TEE_IMPL_ID_QSEECOM)
			return fd;
		close(fd);
	}

	return -ENODEV;
}

static int lookup_application(int fd, const char *application,
			      uint32_t *session_id, uint32_t *tee_result)
{
	struct tee_ioctl_shm_alloc_data allocation = { .size = APP_NAME_MAX };
	uint64_t storage[(sizeof(struct tee_ioctl_open_session_arg) +
			  sizeof(struct tee_ioctl_param) + 7) / 8] = {};
	struct tee_ioctl_open_session_arg *session = (void *)storage;
	struct tee_ioctl_param *params = session->params;
	struct tee_ioctl_buf_data data;
	void *name_memory;
	int saved_errno;
	int shm_fd;

	shm_fd = ioctl(fd, TEE_IOC_SHM_ALLOC, &allocation);
	if (shm_fd < 0)
		return -errno;
	name_memory = mmap(NULL, allocation.size, PROT_READ | PROT_WRITE,
			   MAP_SHARED, shm_fd, 0);
	close(shm_fd);
	if (name_memory == MAP_FAILED)
		return -errno;

	memset(name_memory, 0, allocation.size);
	memcpy(name_memory, application, strlen(application));
	session->num_params = 1;
	params[0].attr = TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INPUT;
	params[0].b = strlen(application) + 1;
	params[0].c = allocation.id;
	data.buf_ptr = (uintptr_t)storage;
	data.buf_len = sizeof(storage);
	if (ioctl(fd, TEE_IOC_OPEN_SESSION, &data)) {
		saved_errno = errno;
		munmap(name_memory, allocation.size);
		return -saved_errno;
	}

	munmap(name_memory, allocation.size);
	*session_id = session->session;
	*tee_result = session->ret;
	return session->ret ? -EPROTO : 0;
}

int main(int argc, char **argv)
{
	uint32_t session_id = 0;
	uint32_t tee_result = 0;
	int fd;
	int result;

	if (argc != 2 || !argv[1][0] || strlen(argv[1]) >= APP_NAME_MAX ||
	    strchr(argv[1], '/')) {
		fprintf(stderr, "usage: %s APPLICATION\n", argv[0]);
		return 2;
	}

	fd = open_qseecom_client();
	if (fd < 0) {
		fprintf(stderr,
			"event=qsee_lookup app=%s present=unknown stage=device errno=%d\n",
			argv[1], -fd);
		return 1;
	}

	result = lookup_application(fd, argv[1], &session_id, &tee_result);
	if (result) {
		fprintf(stderr,
			"event=qsee_lookup app=%s present=false errno=%d tee_result=%u\n",
			argv[1], -result, tee_result);
		close(fd);
		return result == -ENOENT ? 3 : 1;
	}

	printf("event=qsee_lookup app=%s present=true session=%u tee_result=0\n",
	       argv[1], session_id);
	close(fd);
	return 0;
}
