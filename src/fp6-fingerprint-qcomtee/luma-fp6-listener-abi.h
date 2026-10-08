/* SPDX-License-Identifier: Apache-2.0 */
#ifndef LUMA_FP6_LISTENER_ABI_H
#define LUMA_FP6_LISTENER_ABI_H

#include <linux/ioctl.h>
#include <linux/types.h>

#define LUMA_FP6_LISTENER_ABI_VERSION 1U
struct luma_fp6_listener_message {
	__u32 abi_version;
	__u32 listener_id;
	__u64 sequence;
	__u32 buffer_size;
	__s32 dispatch_status;
	__u64 payload_ptr;
	__u32 payload_capacity;
	__u32 reserved;
};

#define LUMA_FP6_LISTENER_IOC_MAGIC 0xf7
#define LUMA_FP6_LISTENER_IOC_WAIT \
	_IOWR(LUMA_FP6_LISTENER_IOC_MAGIC, 0x20, \
	      struct luma_fp6_listener_message)
#define LUMA_FP6_LISTENER_IOC_RESPOND \
	_IOW(LUMA_FP6_LISTENER_IOC_MAGIC, 0x21, \
	     struct luma_fp6_listener_message)

#endif
