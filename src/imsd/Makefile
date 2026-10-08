# SPDX-License-Identifier: GPL-3.0-only
# SPDX-FileCopyrightText: Copyright (C) 2026 Catcrafts®
#
# Distro-packaging build path. project.cpp (crafter-build) remains the
# canonical build description; this Makefile mirrors it for environments
# where crafter-build isn't packaged — same clang module pipeline, same
# flags. Keep the two in sync when products, module partitions or link
# flags change.
#
#   make          -> build/make/{imsd,imsd-media,imsd-dialerd}
#   make check    -> build + run the 7 unit-test suites
#   make install  -> DESTDIR/PREFIX staged install (binaries + systemd
#                    unit, D-Bus policy, dialerd autostart, ims-pdn-up.sh)
#
# Requires clang++ with libc++ and the libc++ std module sources
# (std.cppm — Alpine: llvm-runtimes, Arch: libc++). Override STD_CPPM if
# yours lives elsewhere.

# make's builtin default is CXX=g++, and distro build environments (abuild)
# export CXX=c++ — both gcc, which has no --precompile. This build requires
# clang: the file assignment deliberately ignores the environment while still
# yielding to an explicit `make CXX=...`.
CXX = clang++
AR         ?= ar
PKG_CONFIG ?= pkg-config
STD_CPPM   ?= /usr/share/libc++/v1/std.cppm

O   ?= build/make
PCM  = $(O)/pcm
OBJ  = $(O)/obj

OPT ?= -O3
# One flag set for every TU: BMIs are rejected on compiler-option mismatch,
# so per-target extras are limited to -I/-D (compile) and -l/-L (link).
ALL_CXXFLAGS = -std=c++26 -stdlib=libc++ $(OPT) $(CXXFLAGS)
ALL_LDFLAGS  = -fuse-ld=lld $(LDFLAGS)

# pkg-config gio-2.0: only -I/-D pass through — it also emits -pthread,
# which would config-mismatch the shared std BMI (project.cpp filters
# identically). pthread lives in libc on every platform we target.
GIO_CFLAGS := $(filter -I% -D%,$(shell $(PKG_CONFIG) --cflags gio-2.0))
GIO_LIBS   := $(filter -l% -L%,$(shell $(PKG_CONFIG) --libs gio-2.0))
ifeq ($(strip $(GIO_LIBS)),)
GIO_LIBS := -lgio-2.0 -lgobject-2.0 -lglib-2.0
endif

# Imsd module partitions, plus the primary interface last. The BMI file
# names must stay <module>-<partition>.pcm — that is how clang resolves
# `import :Part;` under -fprebuilt-module-path.
PARTITIONS   = Imsd-Util Imsd-Sip Imsd-Sdp Imsd-Aka Imsd-Ipsec \
               Imsd-Messages Imsd-Engine
CORE_MODULES = $(PARTITIONS) Imsd
CORE_OBJS    = $(CORE_MODULES:%=$(OBJ)/%.o)

TESTS = Util Aka Ipsec Messages Engine Sip Sdp

all: $(O)/imsd $(O)/imsd-media $(O)/imsd-dialerd

# ---- module BMIs ----------------------------------------------------------

$(PCM)/std.pcm: $(STD_CPPM) | $(PCM)
	$(CXX) $(ALL_CXXFLAGS) -Wno-reserved-identifier \
	    -Wno-reserved-module-identifier --precompile $< -o $@

$(PCM)/%.pcm: interfaces/%.cppm $(PCM)/std.pcm | $(PCM)
	$(CXX) $(ALL_CXXFLAGS) -fprebuilt-module-path=$(PCM) --precompile $< -o $@

# partition import graph (see the `import :X;` lines in interfaces/)
$(PCM)/Imsd-Aka.pcm $(PCM)/Imsd-Ipsec.pcm: $(PCM)/Imsd-Util.pcm
$(PCM)/Imsd-Messages.pcm: $(PCM)/Imsd-Util.pcm $(PCM)/Imsd-Aka.pcm \
    $(PCM)/Imsd-Sip.pcm
$(PCM)/Imsd-Engine.pcm: $(PCM)/Imsd-Util.pcm $(PCM)/Imsd-Sip.pcm \
    $(PCM)/Imsd-Sdp.pcm $(PCM)/Imsd-Messages.pcm
$(PCM)/Imsd.pcm: $(PARTITIONS:%=$(PCM)/%.pcm)

# ---- imsd-core (static lib of the pure-C++ engine modules) ----------------

$(OBJ)/%.o: $(PCM)/%.pcm | $(OBJ)
	$(CXX) $(ALL_CXXFLAGS) -fprebuilt-module-path=$(PCM) -c $< -o $@

$(O)/libimsd-core.a: $(CORE_OBJS)
	$(AR) rcs $@ $^

# ---- executables -----------------------------------------------------------

$(OBJ)/main.o: implementations/main.cpp $(PCM)/Imsd.pcm | $(OBJ)
	$(CXX) $(ALL_CXXFLAGS) $(GIO_CFLAGS) -fprebuilt-module-path=$(PCM) \
	    -c $< -o $@

$(O)/imsd: $(OBJ)/main.o $(O)/libimsd-core.a
	$(CXX) $(ALL_CXXFLAGS) $(ALL_LDFLAGS) $^ $(GIO_LIBS) -lpthread -o $@

$(OBJ)/media.o: implementations/media.cpp $(PCM)/std.pcm | $(OBJ)
	$(CXX) $(ALL_CXXFLAGS) -fprebuilt-module-path=$(PCM) -c $< -o $@

$(O)/imsd-media: $(OBJ)/media.o
	$(CXX) $(ALL_CXXFLAGS) $(ALL_LDFLAGS) $^ -ldl -lpthread -o $@

$(OBJ)/dialerd.o: implementations/dialerd.cpp $(PCM)/std.pcm | $(OBJ)
	$(CXX) $(ALL_CXXFLAGS) $(GIO_CFLAGS) -fprebuilt-module-path=$(PCM) \
	    -c $< -o $@

$(O)/imsd-dialerd: $(OBJ)/dialerd.o
	$(CXX) $(ALL_CXXFLAGS) $(ALL_LDFLAGS) $^ $(GIO_LIBS) -o $@

# ---- tests -----------------------------------------------------------------

$(O)/tests/%: tests/%/main.cpp $(PCM)/Imsd.pcm $(O)/libimsd-core.a | $(O)/tests
	$(CXX) $(ALL_CXXFLAGS) $(ALL_LDFLAGS) -fprebuilt-module-path=$(PCM) \
	    $< $(O)/libimsd-core.a -lpthread -o $@

check: $(TESTS:%=$(O)/tests/%)
	@fail=0; for t in $(TESTS); do \
	    echo "== $$t"; $(O)/tests/$$t || fail=1; \
	done; exit $$fail

# ---- install ----------------------------------------------------------------

PREFIX ?= /usr

install: all
	install -Dm755 $(O)/imsd $(DESTDIR)$(PREFIX)/bin/imsd
	install -Dm755 $(O)/imsd-dialerd $(DESTDIR)$(PREFIX)/bin/imsd-dialerd
	install -Dm755 $(O)/imsd-media $(DESTDIR)$(PREFIX)/libexec/imsd-media
	install -Dm755 packaging/ims-pdn-up.sh \
	    $(DESTDIR)$(PREFIX)/libexec/ims-pdn-up.sh
	install -Dm644 packaging/imsd.service \
	    $(DESTDIR)$(PREFIX)/lib/systemd/system/imsd.service
	install -Dm644 packaging/imsd-dialerd.desktop \
	    $(DESTDIR)/etc/xdg/autostart/imsd-dialerd.desktop
	install -Dm644 packaging/net.catcrafts.IMS1.conf \
	    $(DESTDIR)$(PREFIX)/share/dbus-1/system.d/net.catcrafts.IMS1.conf

$(PCM) $(OBJ) $(O)/tests:
	mkdir -p $@

clean:
	rm -rf $(O)

.PHONY: all check install clean
