#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Backport only the owned native Piper module into pinned SpeechD0.12.1."""
from pathlib import Path
import shutil,sys
root=Path(sys.argv[1]);own=Path(sys.argv[2]);modules=root/'src/modules'
shutil.copyfile(own/'vendor/piper.cpp',modules/'piper.cpp')
p=modules/'Makefile.am'
with p.open('a') as stream:
 stream.write('''
# Luma: upstream persistent native Piper module, system ONNX/espeak library.
modulebin_PROGRAMS += sd_piper
sd_piper_SOURCES = piper.cpp $(common_SOURCES) module_utils_addvoice.c module_utils_play.c
sd_piper_CPPFLAGS = $(AM_CPPFLAGS) $(DOTCONF_CFLAGS) $(SNDFILE_CFLAGS) $(PIPER_CFLAGS)
sd_piper_CXXFLAGS = $(AM_CXXFLAGS) -std=c++17
sd_piper_LDADD = $(top_builddir)/src/common/libcommon.la $(audio_dlopen_modules) $(common_LDADD) $(SNDFILE_LIBS) $(PIPER_LIBS)
''')
