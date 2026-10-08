# SPDX-License-Identifier: Apache-2.0

CC ?= aarch64-linux-gnu-gcc
CFLAGS ?= -O2 -Wall -Wextra -Werror

.PHONY: all clean

all: qsee-app-lookup

qsee-app-lookup: qsee_app_lookup.c
	$(CC) $(CFLAGS) -static -o $@ $<

clean:
	rm -f qsee-app-lookup
