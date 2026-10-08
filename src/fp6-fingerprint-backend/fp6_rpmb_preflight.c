/* SPDX-License-Identifier: BSD-3-Clause-Clear */
#include "rpmb.h"

#include <stdint.h>
#include <stdio.h>

int main(void)
{
	rpmb_init_info_t info = {};
	int result;

	result = rpmb_init(&info);
	if (result) {
		fprintf(stderr, "event=rpmb_preflight status=%d\n", result);
		return 1;
	}
	printf("event=rpmb_preflight status=0 device_type=%u sectors=%u "
	       "reliable_write_frames=%u\n",
	       info.dev_type, info.size, info.rel_wr_count);
	return 0;
}
