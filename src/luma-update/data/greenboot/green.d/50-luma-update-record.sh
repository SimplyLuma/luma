#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# All required health checks passed: record that a staged update booted.
/usr/libexec/luma-update-boot green || :
exit 0
