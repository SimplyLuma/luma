#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# A required health check failed on this boot. greenboot's boot counter decides
# when to fall back; the next boot records the rollback.
/usr/libexec/luma-update-boot red || :
exit 0
