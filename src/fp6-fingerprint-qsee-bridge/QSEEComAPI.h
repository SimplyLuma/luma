/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Narrow ABI subset of AOSP hardware/qcom/keymaster/QSEEComAPI.h at
 * 73a9f7a89a41c13486e925d6709fdf7a0d9665aa.  Luma deliberately exposes
 * only the three symbols imported by the archived FP6 FocalTech HAL.
 */

#ifndef LUMA_FP6_QSEECOM_API_H
#define LUMA_FP6_QSEECOM_API_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

struct QSEECom_handle {
	unsigned char *ion_sbuffer;
};

int QSEECom_start_app(struct QSEECom_handle **clnt_handle, const char *path,
		      const char *fname, uint32_t sb_size);
int QSEECom_shutdown_app(struct QSEECom_handle **handle);
int QSEECom_send_cmd(struct QSEECom_handle *handle, void *send_buf,
		     uint32_t sbuf_len, void *rcv_buf, uint32_t rbuf_len);

#ifdef __cplusplus
}
#endif

#endif
