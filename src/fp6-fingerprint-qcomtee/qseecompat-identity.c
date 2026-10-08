// SPDX-License-Identifier: Apache-2.0
/*
 * Bounded FP6 QSEEComCompat identity client.
 *
 * This client exercises only Qualcomm's object-based QSEECom compatibility
 * app loader. It registers a real UID/time credential callback, opens service
 * UID 122, looks up one allow-listed non-biometric control TA, and optionally
 * loads its reconstructed split ELF. It never invokes the returned TA object.
 */

#define _GNU_SOURCE

#include <elf.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>

#include <qcomtee_object.h>

#define QSEECOM_COMPAT_APP_LOADER_UID 122U
#define CLIENT_ENV_REGISTER 2U
#define CLIENT_ENV_OPEN 0U
#define APP_LOADER_LOAD_FROM_BUFFER 1U
#define APP_LOADER_LOOKUP_TA 2U
#define APP_COMPAT_UNLOAD 2U
#define CREDENTIAL_GET_LENGTH 0U
#define CREDENTIAL_READ_AT_OFFSET 1U
#define MAX_ELF_SIZE (64U * 1024U * 1024U)
#define MAX_SPLITS 64U

struct runner_state {
	pthread_t thread;
	struct qcomtee_object *root;
};

struct credential_object {
	struct qcomtee_object object;
	uint8_t cbor[32];
	size_t size;
};

static int tee_call(int fd, unsigned long op, ...)
{
	va_list ap;
	void *arg;
	int ret;

	va_start(ap, op);
	arg = va_arg(ap, void *);
	va_end(ap);

	pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL);
	ret = ioctl(fd, op, arg);
	pthread_setcanceltype(PTHREAD_CANCEL_DEFERRED, NULL);
	return ret;
}

static void *supplicant_worker(void *arg)
{
	struct runner_state *state = arg;

	while (!qcomtee_object_process_one(state->root))
		pthread_testcancel();
	return NULL;
}

static void runner_release(void *arg)
{
	struct runner_state *state = arg;

	if (state->thread) {
		pthread_cancel(state->thread);
		pthread_join(state->thread, NULL);
	}
	free(state);
}

static size_t cbor_uint(uint8_t *out, uint64_t value)
{
	if (value < 24) {
		out[0] = (uint8_t)value;
		return 1;
	}
	if (value <= UINT8_MAX) {
		out[0] = 0x18;
		out[1] = (uint8_t)value;
		return 2;
	}
	if (value <= UINT16_MAX) {
		out[0] = 0x19;
		out[1] = (uint8_t)(value >> 8);
		out[2] = (uint8_t)value;
		return 3;
	}
	if (value <= UINT32_MAX) {
		out[0] = 0x1a;
		out[1] = (uint8_t)(value >> 24);
		out[2] = (uint8_t)(value >> 16);
		out[3] = (uint8_t)(value >> 8);
		out[4] = (uint8_t)value;
		return 5;
	}
	out[0] = 0x1b;
	for (size_t i = 0; i < 8; i++)
		out[i + 1] = (uint8_t)(value >> (56 - (i * 8)));
	return 9;
}

static qcomtee_result_t credential_dispatch(struct qcomtee_object *object,
					     qcomtee_op_t op,
					     struct qcomtee_param *params,
					     int num)
{
	struct credential_object *credential =
		container_of(object, struct credential_object, object);

	if (op == CREDENTIAL_GET_LENGTH) {
		if (num != 1 || params[0].attr != QCOMTEE_UBUF_OUTPUT)
			return QCOMTEE_ERROR_INVALID;
		params[0].ubuf.addr = &credential->size;
		params[0].ubuf.size = sizeof(credential->size);
		return QCOMTEE_OK;
	}

	if (op == CREDENTIAL_READ_AT_OFFSET) {
		uint64_t offset;
		size_t available;

		if (num != 2 || params[0].attr != QCOMTEE_UBUF_INPUT ||
		    params[1].attr != QCOMTEE_UBUF_OUTPUT ||
		    params[0].ubuf.size != sizeof(offset))
			return QCOMTEE_ERROR_INVALID;
		memcpy(&offset, params[0].ubuf.addr, sizeof(offset));
		if (offset >= credential->size)
			return QCOMTEE_ERROR_INVALID;
		available = credential->size - (size_t)offset;
		if (params[1].ubuf.size > available)
			params[1].ubuf.size = available;
		params[1].ubuf.addr = credential->cbor + offset;
		return QCOMTEE_OK;
	}

	return QCOMTEE_ERROR_INVALID;
}

static void credential_release(struct qcomtee_object *object)
{
	free(container_of(object, struct credential_object, object));
}

static struct qcomtee_object *credential_new(struct qcomtee_object *root)
{
	static struct qcomtee_object_ops ops = {
		.release = credential_release,
		.dispatch = credential_dispatch,
	};
	struct credential_object *credential;
	struct timeval tv;
	uint64_t milliseconds;
	size_t cursor = 0;

	credential = calloc(1, sizeof(*credential));
	if (!credential)
		return QCOMTEE_OBJECT_NULL;
	if (qcomtee_object_cb_init(&credential->object, &ops, root)) {
		free(credential);
		return QCOMTEE_OBJECT_NULL;
	}

	gettimeofday(&tv, NULL);
	milliseconds = (uint64_t)tv.tv_sec * 1000U + (uint64_t)tv.tv_usec / 1000U;
	credential->cbor[cursor++] = 0xa2; /* map(2) */
	cursor += cbor_uint(credential->cbor + cursor, 1); /* uid key */
	cursor += cbor_uint(credential->cbor + cursor, (uint64_t)getuid());
	cursor += cbor_uint(credential->cbor + cursor, 6); /* system-time key */
	cursor += cbor_uint(credential->cbor + cursor, milliseconds);
	credential->size = cursor;
	return &credential->object;
}

static struct qcomtee_object *root_new(const char *device)
{
	struct runner_state *state;
	struct qcomtee_object *root;

	state = calloc(1, sizeof(*state));
	if (!state)
		return QCOMTEE_OBJECT_NULL;
	root = qcomtee_object_root_init(device, tee_call, runner_release, state);
	if (root == QCOMTEE_OBJECT_NULL) {
		free(state);
		return QCOMTEE_OBJECT_NULL;
	}
	state->root = root;
	if (pthread_create(&state->thread, NULL, supplicant_worker, state)) {
		qcomtee_object_refs_dec(root);
		return QCOMTEE_OBJECT_NULL;
	}
	return root;
}

static struct qcomtee_object *client_env_new(struct qcomtee_object *root)
{
	struct qcomtee_object *credential = credential_new(root);
	struct qcomtee_param params[2] = { 0 };
	qcomtee_result_t result = QCOMTEE_ERROR;

	if (credential == QCOMTEE_OBJECT_NULL)
		return QCOMTEE_OBJECT_NULL;
	params[0].attr = QCOMTEE_OBJREF_INPUT;
	params[0].object = credential;
	params[1].attr = QCOMTEE_OBJREF_OUTPUT;
	if (qcomtee_object_invoke(root, CLIENT_ENV_REGISTER, params, 2, &result)) {
		qcomtee_object_refs_dec(credential);
		return QCOMTEE_OBJECT_NULL;
	}
	if (result != QCOMTEE_OK) {
		fprintf(stderr, "client registration result=%d\n", (int32_t)result);
		return QCOMTEE_OBJECT_NULL;
	}
	return params[1].object;
}

static struct qcomtee_object *service_open(struct qcomtee_object *client_env,
					    uint32_t uid)
{
	struct qcomtee_param params[2] = { 0 };
	qcomtee_result_t result = QCOMTEE_ERROR;

	params[0].attr = QCOMTEE_UBUF_INPUT;
	params[0].ubuf.addr = &uid;
	params[0].ubuf.size = sizeof(uid);
	params[1].attr = QCOMTEE_OBJREF_OUTPUT;
	if (qcomtee_object_invoke(client_env, CLIENT_ENV_OPEN, params, 2,
				  &result) || result != QCOMTEE_OK) {
		fprintf(stderr, "service open uid=%u result=%d\n", uid,
			(int32_t)result);
		return QCOMTEE_OBJECT_NULL;
	}
	return params[1].object;
}

static int32_t lookup_ta(struct qcomtee_object *loader, const char *name,
			 struct qcomtee_object **controller, uint32_t *arch)
{
	struct qcomtee_param params[3] = { 0 };
	qcomtee_result_t result = QCOMTEE_ERROR;

	params[0].attr = QCOMTEE_UBUF_INPUT;
	params[0].ubuf.addr = (void *)name;
	params[0].ubuf.size = strlen(name);
	params[1].attr = QCOMTEE_UBUF_OUTPUT;
	params[1].ubuf.addr = arch;
	params[1].ubuf.size = sizeof(*arch);
	params[2].attr = QCOMTEE_OBJREF_OUTPUT;
	if (qcomtee_object_invoke(loader, APP_LOADER_LOOKUP_TA, params, 3,
				  &result))
		return -1;
	if (result == QCOMTEE_OK)
		*controller = params[2].object;
	return (int32_t)result;
}

static int read_file(const char *path, uint8_t **data, size_t *size)
{
	struct stat st;
	uint8_t *buffer;
	ssize_t done = 0;
	int fd;

	fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
	if (fd < 0 || fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size <= 0 ||
	    (uint64_t)st.st_size > MAX_ELF_SIZE) {
		if (fd >= 0)
			close(fd);
		return -1;
	}
	buffer = malloc((size_t)st.st_size);
	if (!buffer) {
		close(fd);
		return -1;
	}
	while ((size_t)done < (size_t)st.st_size) {
		ssize_t count = read(fd, buffer + done, (size_t)st.st_size - (size_t)done);
		if (count <= 0) {
			free(buffer);
			close(fd);
			return -1;
		}
		done += count;
	}
	close(fd);
	*data = buffer;
	*size = (size_t)st.st_size;
	return 0;
}

static int split_path(char *path, size_t path_size, const char *directory,
		      const char *name, unsigned int split)
{
	int count = snprintf(path, path_size, "%s/%s.b%02u", directory, name,
			     split);

	return count < 0 || (size_t)count >= path_size ? -1 : 0;
}

static int reconstruct_elf(const char *directory, const char *name,
			   uint8_t **elf, size_t *elf_size)
{
	char path[1024];
	uint8_t *header = NULL;
	size_t header_size = 0;
	size_t offsets[MAX_SPLITS] = { 0 };
	unsigned int split_count;

	if (split_path(path, sizeof(path), directory, name, 0) ||
	    read_file(path, &header, &header_size))
		return -1;
	if (header_size < sizeof(Elf64_Ehdr) || memcmp(header, ELFMAG, SELFMAG) ||
	    header[EI_CLASS] != ELFCLASS64) {
		free(header);
		return -1;
	}

	Elf64_Ehdr ehdr;
	memcpy(&ehdr, header, sizeof(ehdr));
	split_count = ehdr.e_phnum;
	if (split_count < 2 || split_count > MAX_SPLITS ||
	    ehdr.e_phentsize != sizeof(Elf64_Phdr) ||
	    ehdr.e_phoff > header_size ||
	    (size_t)split_count > (header_size - (size_t)ehdr.e_phoff) /
				      sizeof(Elf64_Phdr)) {
		free(header);
		return -1;
	}
	for (unsigned int i = 1; i < split_count; i++) {
		Elf64_Phdr phdr;
		memcpy(&phdr, header + ehdr.e_phoff + i * sizeof(phdr),
		       sizeof(phdr));
		/* Qualcomm split ELFs may intentionally reuse or reorder offsets. */
		if (phdr.p_offset > MAX_ELF_SIZE) {
			free(header);
			return -1;
		}
		offsets[i] = (size_t)phdr.p_offset;
	}

	uint8_t *last = NULL;
	size_t last_size = 0;
	if (split_path(path, sizeof(path), directory, name, split_count - 1) ||
	    read_file(path, &last, &last_size) ||
	    offsets[split_count - 1] > MAX_ELF_SIZE - last_size) {
		free(header);
		free(last);
		return -1;
	}
	*elf_size = offsets[split_count - 1] + last_size;
	*elf = calloc(1, *elf_size);
	if (!*elf || header_size > *elf_size) {
		free(*elf);
		free(header);
		free(last);
		return -1;
	}
	memcpy(*elf, header, header_size);
	free(header);

	for (unsigned int i = 1; i < split_count; i++) {
		uint8_t *part = NULL;
		size_t part_size = 0;

		if (i == split_count - 1) {
			part = last;
			part_size = last_size;
		} else if (split_path(path, sizeof(path), directory, name, i) ||
			   read_file(path, &part, &part_size)) {
			free(*elf);
			free(last);
			return -1;
		}
		if (offsets[i] > *elf_size || part_size > *elf_size - offsets[i]) {
			free(part);
			free(*elf);
			return -1;
		}
		memcpy(*elf + offsets[i], part, part_size);
		free(part);
	}
	return 0;
}

static int load_ta(struct qcomtee_object *loader, const char *directory,
		   const char *name, struct qcomtee_object **controller)
{
	struct qcomtee_param params[4] = { 0 };
	qcomtee_result_t result = QCOMTEE_ERROR;
	uint8_t *elf = NULL;
	size_t elf_size = 0;
	char distinguished_name[128] = { 0 };

	if (reconstruct_elf(directory, name, &elf, &elf_size)) {
		fprintf(stderr, "split ELF reconstruction failed\n");
		return -1;
	}
	params[0].attr = QCOMTEE_UBUF_INPUT;
	params[0].ubuf.addr = elf;
	params[0].ubuf.size = elf_size;
	params[1].attr = QCOMTEE_UBUF_INPUT;
	params[1].ubuf.addr = (void *)name;
	params[1].ubuf.size = strlen(name);
	params[2].attr = QCOMTEE_UBUF_OUTPUT;
	params[2].ubuf.addr = distinguished_name;
	params[2].ubuf.size = sizeof(distinguished_name) - 1;
	params[3].attr = QCOMTEE_OBJREF_OUTPUT;
	if (qcomtee_object_invoke(loader, APP_LOADER_LOAD_FROM_BUFFER, params, 4,
				  &result)) {
		free(elf);
		return -1;
	}
	free(elf);
	if (result != QCOMTEE_OK) {
		fprintf(stderr, "loadFromBuffer result=%d\n", (int32_t)result);
		return (int32_t)result;
	}
	if (params[2].ubuf.size >= sizeof(distinguished_name))
		params[2].ubuf.size = sizeof(distinguished_name) - 1;
	distinguished_name[params[2].ubuf.size] = '\0';
	*controller = params[3].object;
	printf("identity accepted app=%s distinguished_name=%s elf_bytes=%zu\n",
	       name, distinguished_name, elf_size);
	return 0;
}

static int unload_ta(struct qcomtee_object *controller)
{
	qcomtee_result_t result = QCOMTEE_ERROR;

	if (qcomtee_object_invoke(controller, APP_COMPAT_UNLOAD, NULL, 0,
				  &result))
		return -1;
	if (result != QCOMTEE_OK) {
		fprintf(stderr, "control unload result=%d\n", (int32_t)result);
		return -1;
	}
	printf("control TA unloaded\n");
	return 0;
}

int main(int argc, char **argv)
{
	struct qcomtee_object *root = QCOMTEE_OBJECT_NULL;
	struct qcomtee_object *client_env = QCOMTEE_OBJECT_NULL;
	struct qcomtee_object *loader = QCOMTEE_OBJECT_NULL;
	struct qcomtee_object *controller = QCOMTEE_OBJECT_NULL;
	const char *device;
	const char *directory;
	const char *name;
	bool lookup_only;
	uint32_t arch = 0;
	int32_t result;
	int exit_status = 1;

	if (argc != 5 || (strcmp(argv[1], "--lookup-only") &&
			  strcmp(argv[1], "--load-control"))) {
		fprintf(stderr, "usage: %s --lookup-only|--load-control /dev/teeN FIRMWARE_DIR smplap64\n",
			argv[0]);
		return 2;
	}
	lookup_only = !strcmp(argv[1], "--lookup-only");
	device = argv[2];
	directory = argv[3];
	name = argv[4];
	if (strcmp(name, "smplap64")) {
		fprintf(stderr, "only the non-biometric smplap64 control is allowed\n");
		return 2;
	}

	root = root_new(device);
	if (root == QCOMTEE_OBJECT_NULL) {
		perror("qcomtee root");
		goto out;
	}
	client_env = client_env_new(root);
	if (client_env == QCOMTEE_OBJECT_NULL)
		goto out;
	printf("credentialed client environment accepted uid=%lu\n",
	       (unsigned long)getuid());
	loader = service_open(client_env, QSEECOM_COMPAT_APP_LOADER_UID);
	if (loader == QCOMTEE_OBJECT_NULL)
		goto out;
	printf("QSEEComCompat app-loader service accepted uid=%u\n",
	       QSEECOM_COMPAT_APP_LOADER_UID);

	result = lookup_ta(loader, name, &controller, &arch);
	if (result == QCOMTEE_OK) {
		printf("lookup accepted app=%s arch=%u\n", name, arch);
		exit_status = 0;
		goto out;
	}
	printf("lookup result=%d app=%s\n", result, name);
	if (lookup_only) {
		exit_status = result == 23 ? 0 : 1;
		goto out;
	}
	if (load_ta(loader, directory, name, &controller))
		goto out;
	printf("control TA object returned; no TA operation invoked\n");
	if (unload_ta(controller))
		goto out;
	exit_status = 0;

out:
	qcomtee_object_refs_dec(controller);
	qcomtee_object_refs_dec(loader);
	qcomtee_object_refs_dec(client_env);
	qcomtee_object_refs_dec(root);
	return exit_status;
}
