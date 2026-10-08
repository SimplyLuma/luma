/* SPDX-License-Identifier: Apache-2.0 */

#include "QSEEComAPI.h"
#include "qseecom_bridge_internal.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/tee.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#define TEST_TEE_FD 7
#define TEST_SHM_FD_BASE 100
#define TEST_SHM_COUNT 16
#define TEST_QSEE_IMPL_ID 5u

struct fake_shm {
	int fd;
	int id;
	void *address;
	size_t size;
};

static struct fake_shm shared[TEST_SHM_COUNT];
static int next_shm;
static int tee_closes;
static int invocations;
static int fail_session;

static void test_fail(const char *expression, const char *file, int line)
{
	fprintf(stderr, "FAIL: %s (%s:%d)\n", expression, file, line);
	exit(1);
}

#define CHECK(expression) \
	do { if (!(expression)) test_fail(#expression, __FILE__, __LINE__); } while (0)

static struct fake_shm *shm_by_fd(int fd)
{
	for (int i = 0; i < next_shm; i++)
		if (shared[i].fd == fd)
			return &shared[i];
	return NULL;
}

static struct fake_shm *shm_by_id(int id)
{
	for (int i = 0; i < next_shm; i++)
		if (shared[i].id == id)
			return &shared[i];
	return NULL;
}

static int fake_open(const char *path, int flags)
{
	CHECK(flags == (O_RDWR | O_CLOEXEC));
	if (!strcmp(path, "/dev/tee0"))
		return TEST_TEE_FD;
	errno = ENOENT;
	return -1;
}

static int fake_close(int fd)
{
	if (fd == TEST_TEE_FD)
		tee_closes++;
	return 0;
}

static int fake_alloc(struct tee_ioctl_shm_alloc_data *allocation)
{
	struct fake_shm *memory;

	CHECK(next_shm < TEST_SHM_COUNT);
	memory = &shared[next_shm];
	memory->fd = TEST_SHM_FD_BASE + next_shm;
	memory->id = 1000 + next_shm;
	memory->size = (size_t)allocation->size;
	memory->address = calloc(1, memory->size);
	CHECK(memory->address != NULL);
	allocation->id = memory->id;
	next_shm++;
	return memory->fd;
}

static int fake_open_session(struct tee_ioctl_buf_data *data)
{
	struct tee_ioctl_open_session_arg *request =
		(void *)(uintptr_t)data->buf_ptr;
	struct tee_ioctl_param *params = request->params;
	struct fake_shm *name;

	CHECK(data->buf_len == sizeof(*request) + sizeof(*params));
	CHECK(request->num_params == 1);
	CHECK(params[0].attr == TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INPUT);
	name = shm_by_id((int)params[0].c);
	CHECK(name != NULL);
	CHECK(params[0].b == strlen("focal64") + 1);
	CHECK(!strcmp(name->address, "focal64"));
	if (fail_session) {
		errno = ENOENT;
		return -1;
	}
	request->session = 77;
	request->ret = 0;
	return 0;
}

static int fake_invoke(struct tee_ioctl_buf_data *data)
{
	struct tee_ioctl_invoke_arg *request =
		(void *)(uintptr_t)data->buf_ptr;
	struct tee_ioctl_param *params = request->params;
	struct fake_shm *input;
	struct fake_shm *output;

	CHECK(data->buf_len == sizeof(*request) + 2 * sizeof(*params));
	CHECK(request->func == 0);
	CHECK(request->session == 77);
	CHECK(request->num_params == 2);
	CHECK(params[0].attr == TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_INPUT);
	CHECK(params[1].attr == TEE_IOCTL_PARAM_ATTR_TYPE_MEMREF_OUTPUT);
	input = shm_by_id((int)params[0].c);
	output = shm_by_id((int)params[1].c);
	CHECK(input != NULL && output != NULL);
	CHECK(params[0].b == 4 && params[1].b == 4);
	CHECK(!memcmp(input->address, "PING", 4));
	memcpy(output->address, "PONG", 4);
	request->ret = 0;
	invocations++;
	return 0;
}

static int fake_ioctl(int fd, unsigned long request, void *argument)
{
	CHECK(fd == TEST_TEE_FD);
	if (request == TEE_IOC_VERSION) {
		struct tee_ioctl_version_data *version = argument;
		version->impl_id = TEST_QSEE_IMPL_ID;
		return 0;
	}
	if (request == TEE_IOC_SHM_ALLOC)
		return fake_alloc(argument);
	if (request == TEE_IOC_OPEN_SESSION)
		return fake_open_session(argument);
	if (request == TEE_IOC_INVOKE)
		return fake_invoke(argument);
	test_fail("unexpected ioctl", __FILE__, __LINE__);
	return -1;
}

static void *fake_map(size_t length, int fd)
{
	struct fake_shm *memory = shm_by_fd(fd);
	CHECK(memory != NULL);
	CHECK(memory->size == length);
	return memory->address;
}

static int fake_unmap(void *address, size_t length)
{
	for (int i = 0; i < next_shm; i++) {
		if (shared[i].address != address)
			continue;
		CHECK(shared[i].size == length);
		free(shared[i].address);
		shared[i].address = NULL;
		return 0;
	}
	test_fail("unknown shared mapping", __FILE__, __LINE__);
	return -1;
}

static void assert_no_mappings(void)
{
	for (int i = 0; i < next_shm; i++)
		CHECK(shared[i].address == NULL);
}

int main(void)
{
	const struct luma_qsee_ops ops = {
		.open_device = fake_open,
		.close_fd = fake_close,
		.ioctl_call = fake_ioctl,
		.map_memory = fake_map,
		.unmap_memory = fake_unmap,
	};
	struct QSEECom_handle *handle = NULL;
	char response[4] = {};

	luma_qsee_set_test_ops(&ops);

	CHECK(QSEECom_start_app(NULL, NULL, "focal64", 4096) == -1);
	CHECK(errno == EINVAL);
	CHECK(QSEECom_start_app(&handle, NULL, "../focal64", 4096) == -1);
	CHECK(errno == EINVAL);
	CHECK(QSEECom_start_app(&handle, NULL, "focal64", 0) == -1);
	CHECK(errno == EINVAL);

	fail_session = 1;
	CHECK(QSEECom_start_app(&handle, "/ignored", "focal64", 4096) == -1);
	CHECK(errno == ENOENT);
	CHECK(handle == NULL);
	assert_no_mappings();

	fail_session = 0;
	CHECK(QSEECom_start_app(&handle, "/ignored", "focal64", 4096) == 0);
	CHECK(handle != NULL);
	CHECK(handle->ion_sbuffer != NULL);
	CHECK(QSEECom_send_cmd(handle, "PING", 4, response, 4) == 0);
	CHECK(!memcmp(response, "PONG", 4));
	CHECK(invocations == 1);
	CHECK(QSEECom_send_cmd(handle, NULL, 4, response, 4) == -1);
	CHECK(errno == EINVAL);
	CHECK(QSEECom_shutdown_app(&handle) == 0);
	CHECK(handle == NULL);
	CHECK(tee_closes == 2);
	assert_no_mappings();

	puts("QSEECom bridge transport tests: PASS");
	return 0;
}
