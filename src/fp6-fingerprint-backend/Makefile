# SPDX-License-Identifier: Apache-2.0

CC ?= cc
CFLAGS ?= -O2 -g
WARNINGS := -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion

.PHONY: check probe enrollment-client wake-input clean

test-gatekeeper-protocol: gatekeeper_protocol.c gatekeeper_protocol.h \
		test_gatekeeper_protocol.c
	$(CC) $(CFLAGS) $(WARNINGS) -o $@ \
		gatekeeper_protocol.c test_gatekeeper_protocol.c

test-focal-protocol: focal_protocol.c focal_protocol.h test_focal_protocol.c
	$(CC) $(CFLAGS) $(WARNINGS) -o $@ \
		focal_protocol.c test_focal_protocol.c

test-focal-driver-control: focal_driver_control.c focal_driver_control.h \
		test_focal_driver_control.c
	$(CC) $(CFLAGS) $(WARNINGS) -o $@ \
		focal_driver_control.c test_focal_driver_control.c

test-enrollment-protocol: enrollment_protocol.c enrollment_protocol.h \
		gatekeeper_protocol.c gatekeeper_protocol.h focal_protocol.c \
		focal_protocol.h test_enrollment_protocol.c
	$(CC) $(CFLAGS) $(WARNINGS) -o $@ enrollment_protocol.c \
		gatekeeper_protocol.c focal_protocol.c test_enrollment_protocol.c

check: test-focal-protocol test-focal-driver-control test-gatekeeper-protocol \
		test-enrollment-protocol
	./test-focal-protocol
	./test-focal-driver-control
	./test-gatekeeper-protocol
	./test-enrollment-protocol

fp6-fingerprint-probe: fp6_fingerprint_probe.c focal_protocol.c \
		focal_protocol.h ../fp6-fingerprint-qsee-bridge/qseecom_bridge.c \
		../fp6-fingerprint-qsee-bridge/QSEEComAPI.h \
		../fp6-fingerprint-qsee-bridge/qseecom_bridge_internal.h
	$(CC) $(CFLAGS) $(WARNINGS) -I../fp6-fingerprint-qsee-bridge \
		-o $@ fp6_fingerprint_probe.c focal_protocol.c \
		../fp6-fingerprint-qsee-bridge/qseecom_bridge.c -pthread

probe: fp6-fingerprint-probe

fp6-fingerprint-enroll: fp6_fingerprint_enroll.c enrollment_protocol.c \
		enrollment_protocol.h gatekeeper_protocol.c gatekeeper_protocol.h \
		focal_protocol.c focal_protocol.h focal_driver_control.c \
		focal_driver_control.h \
		../fp6-fingerprint-qcomtee/luma-fp6-enrollment-abi.h \
		../fp6-fingerprint-qsee-bridge/qseecom_bridge.c \
		../fp6-fingerprint-qsee-bridge/QSEEComAPI.h \
		../fp6-fingerprint-qsee-bridge/qseecom_bridge_internal.h
	$(CC) $(CFLAGS) $(WARNINGS) -I../fp6-fingerprint-qcomtee \
		-I../fp6-fingerprint-qsee-bridge \
		-o $@ fp6_fingerprint_enroll.c enrollment_protocol.c \
		gatekeeper_protocol.c focal_protocol.c focal_driver_control.c \
		../fp6-fingerprint-qsee-bridge/qseecom_bridge.c -lpam -pthread

enrollment-client: fp6-fingerprint-enroll

luma-fp6-wake-input: luma_fp6_wake_input.c
	$(CC) $(CFLAGS) $(WARNINGS) -o $@ luma_fp6_wake_input.c

wake-input: luma-fp6-wake-input

clean:
	rm -f test-focal-protocol test-focal-driver-control test-gatekeeper-protocol \
		test-enrollment-protocol \
		fp6-fingerprint-probe fp6-fingerprint-enroll luma-fp6-wake-input
	rm -rf test-focal-protocol.dSYM test-focal-driver-control.dSYM \
		test-gatekeeper-protocol.dSYM test-enrollment-protocol.dSYM \
		fp6-fingerprint-probe.dSYM fp6-fingerprint-enroll.dSYM \
		luma-fp6-wake-input.dSYM
