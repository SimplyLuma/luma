/* SPDX-License-Identifier: GPL-2.0-only */

#ifndef LUMA_FP6_ENROLLMENT_ABI_H
#define LUMA_FP6_ENROLLMENT_ABI_H

#include <linux/ioctl.h>
#include <linux/types.h>

#define LUMA_FP6_ENROLLMENT_ABI_VERSION 1U
#define LUMA_FP6_ENROLLMENT_MAX_PAYLOAD 16320U

enum luma_fp6_secure_service {
	LUMA_FP6_SERVICE_GATEKEEPER = 1,
	LUMA_FP6_SERVICE_FOCAL = 2,
};

struct luma_fp6_secure_exchange {
	__u32 abi_version;
	__u32 service;
	__u32 command;
	__u32 request_size;
	__u32 response_capacity;
	__u32 response_size;
	__s32 secure_status;
	__u32 reserved;
	__u8 payload[LUMA_FP6_ENROLLMENT_MAX_PAYLOAD];
};

#define LUMA_FP6_ENROLLMENT_IOC_MAGIC 0xf6
#define LUMA_FP6_ENROLLMENT_IOC_EXCHANGE \
	_IOWR(LUMA_FP6_ENROLLMENT_IOC_MAGIC, 1, \
	      struct luma_fp6_secure_exchange)

#endif
