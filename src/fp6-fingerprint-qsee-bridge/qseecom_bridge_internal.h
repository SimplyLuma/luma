/* SPDX-License-Identifier: Apache-2.0 */

#ifndef LUMA_FP6_QSEECOM_BRIDGE_INTERNAL_H
#define LUMA_FP6_QSEECOM_BRIDGE_INTERNAL_H

#include <stddef.h>

struct luma_qsee_ops {
	int (*open_device)(const char *path, int flags);
	int (*close_fd)(int fd);
	int (*ioctl_call)(int fd, unsigned long request, void *argument);
	void *(*map_memory)(size_t length, int fd);
	int (*unmap_memory)(void *address, size_t length);
};

#ifdef LUMA_QSEE_TEST
void luma_qsee_set_test_ops(const struct luma_qsee_ops *ops);
#endif

#endif
