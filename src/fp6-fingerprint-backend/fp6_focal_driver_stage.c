/* SPDX-License-Identifier: Apache-2.0 */

#include "focal_driver_control.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define FOCAL_DEVICE "/dev/focaltech_fp"

static int device_ioctl(void *opaque, uint32_t request, uintptr_t argument)
{
	int descriptor = *(const int *)opaque;

	if (ioctl(descriptor, request, argument) < 0)
		return -errno;
	return 0;
}

static int device_sleep(void *opaque, unsigned int milliseconds)
{
	struct timespec remaining = {
		.tv_sec = (time_t)(milliseconds / 1000u),
		.tv_nsec = (long)(milliseconds % 1000u) * 1000000L,
	};

	(void)opaque;
	while (nanosleep(&remaining, &remaining) < 0) {
		if (errno != EINTR)
			return -errno;
	}
	return 0;
}

static int open_verified_device(void)
{
	struct stat path_status;
	struct stat descriptor_status;
	int descriptor;

	if (geteuid() != 0) {
		errno = EPERM;
		return -1;
	}
	if (lstat(FOCAL_DEVICE, &path_status) < 0 ||
	    !S_ISCHR(path_status.st_mode) || path_status.st_uid != 0) {
		errno = ENODEV;
		return -1;
	}
	descriptor = open(FOCAL_DEVICE, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
	if (descriptor < 0)
		return -1;
	if (fstat(descriptor, &descriptor_status) < 0 ||
	    !S_ISCHR(descriptor_status.st_mode) ||
	    descriptor_status.st_uid != 0 ||
	    descriptor_status.st_rdev != path_status.st_rdev) {
		close(descriptor);
		errno = ENODEV;
		return -1;
	}
	return descriptor;
}

static int run_stage(const char *stage,
		     const struct focal_driver_transport *transport)
{
	struct focal_driver_feature feature;
	char version[FOCAL_DRIVER_VERSION_SIZE];
	int result;

	if (!strcmp(stage, "prepare")) {
		result = focal_driver_prepare(transport, &feature, version);
		if (!result)
			printf("event=focal_driver_prepare accepted feature_version=0x%04x driver_version=%.*s\n",
			       feature.version, (int)sizeof(version), version);
		return result;
	}
	if (!strcmp(stage, "prepare-probe")) {
		result = focal_driver_prepare_probe(transport);
		if (!result)
			puts("event=focal_driver_prepare_probe accepted");
		return result;
	}
	if (!strcmp(stage, "retry-probe")) {
		result = focal_driver_retry_probe(transport);
		if (!result)
			puts("event=focal_driver_retry_probe accepted");
		return result;
	}
	if (!strcmp(stage, "finish-probe")) {
		result = focal_driver_finish_probe(transport);
		if (!result)
			puts("event=focal_driver_finish_probe accepted");
		return result;
	}
	if (!strcmp(stage, "cleanup")) {
		result = focal_driver_cleanup(transport);
		if (!result)
			puts("event=focal_driver_cleanup accepted");
		return result;
	}
	return -EINVAL;
}

int main(int argc, char **argv)
{
	struct focal_driver_transport transport;
	int descriptor;
	int result;

	if (argc != 2) {
		fprintf(stderr,
			"usage: %s prepare|prepare-probe|retry-probe|finish-probe|cleanup\n",
			argv[0]);
		return 2;
	}
	descriptor = open_verified_device();
	if (descriptor < 0) {
		fprintf(stderr, "event=focal_driver_open rejected errno=%d\n", errno);
		return 1;
	}
	transport.ioctl = device_ioctl;
	transport.sleep_ms = device_sleep;
	transport.context = &descriptor;
	result = run_stage(argv[1], &transport);
	if (close(descriptor) < 0 && !result)
		result = -errno;
	if (result) {
		fprintf(stderr, "event=focal_driver_stage rejected stage=%s result=%d\n",
			argv[1], result);
		return 1;
	}
	return 0;
}
