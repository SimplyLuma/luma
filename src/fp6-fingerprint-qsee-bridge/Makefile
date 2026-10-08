# SPDX-License-Identifier: Apache-2.0

CC ?= cc
CFLAGS ?= -O2 -g
CPPFLAGS ?=
WARNINGS := -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion
REPRO_FLAGS := \
	"-ffile-prefix-map=$(CURDIR)=/usr/src/luma/fp6-fingerprint-qsee-bridge" \
	"-fdebug-prefix-map=$(CURDIR)=/usr/src/luma/fp6-fingerprint-qsee-bridge"

.PHONY: all android check clean

all: libQSEEComAPI.so

android: libQSEEComAPI.android.so

libQSEEComAPI.so: qseecom_bridge.c QSEEComAPI.h qseecom_bridge_internal.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) $(REPRO_FLAGS) -fPIC \
		-Wl,--no-undefined -Wl,-soname,libQSEEComAPI.so \
		-shared -o $@ qseecom_bridge.c -pthread \
		-Wl,--version-script=qseecom_bridge.map

libQSEEComAPI.android.so: qseecom_bridge.c QSEEComAPI.h qseecom_bridge_internal.h
	@test -n "$(ANDROID_CC)" || { echo 'ANDROID_CC is required' >&2; exit 2; }
	$(ANDROID_CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) $(REPRO_FLAGS) -fPIC \
		-Wl,--no-undefined -Wl,-soname,libQSEEComAPI.so \
		-shared -o $@ qseecom_bridge.c -pthread \
		-Wl,--version-script=qseecom_bridge.map

test-qseecom-bridge: test_qseecom_bridge.c qseecom_bridge.c QSEEComAPI.h qseecom_bridge_internal.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) $(REPRO_FLAGS) -DLUMA_QSEE_TEST \
		-o $@ test_qseecom_bridge.c qseecom_bridge.c -pthread

check: test-qseecom-bridge
	./test-qseecom-bridge

clean:
	rm -f libQSEEComAPI.so libQSEEComAPI.android.so test-qseecom-bridge
