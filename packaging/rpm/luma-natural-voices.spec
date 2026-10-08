# SPDX-License-Identifier: Apache-2.0
Name: luma-natural-voices
Version: 1.0.0
Release: 1.luma.4.creator20261007.1%{?dist}
Summary: Local natural English voice for Leaf and accessible applications
License: Apache-2.0 AND GPL-3.0-or-later AND LGPL-2.1-or-later AND BSD-2-Clause AND MIT AND LicenseRef-LJSpeech-Public-Domain
URL: https://projectluma.org
Source0: luma-natural-voices.tar.gz
Source1: piper1-gpl-1.4.2.tar.gz
Source2: speechd-0.12.1.tar.gz
Source3: en_US-ljspeech-medium.onnx
Source4: en_US-ljspeech-medium.onnx.json
BuildRequires: gcc-c++
BuildRequires: cmake
BuildRequires: make
BuildRequires: autoconf
BuildRequires: automake
BuildRequires: libtool
BuildRequires: gettext-devel
BuildRequires: pkgconfig
BuildRequires: patch
BuildRequires: espeak-ng
BuildRequires: espeak-ng-devel
BuildRequires: onnxruntime-devel
BuildRequires: rubberband-devel
BuildRequires: glib2-devel
BuildRequires: dotconf-devel
BuildRequires: libsndfile-devel
BuildRequires: speech-dispatcher-devel = 0.12.1
BuildRequires: speech-dispatcher = 0.12.1
BuildRequires: python3-devel
BuildRequires: python3-gobject
BuildRequires: python3-speechd
BuildRequires: luma-developer-platform >= 0.1.0-1.luma.95.creator20261006.1
BuildRequires: desktop-file-utils
BuildRequires: libappstream-glib
BuildRequires: dbus-daemon
BuildRequires: xorg-x11-server-Xvfb
Requires: speech-dispatcher = 0.12.1
Requires: espeak-ng
Requires: python3-speechd
Requires: python3-gobject
Requires: luma-developer-platform >= 0.1.0-1.luma.95.creator20261006.1
Requires: luma-leaf >= 0.1.0-1.luma.8.creator20261005.1
ExclusiveArch: x86_64 aarch64

%description
An optional managed LJSpeech medium natural English voice. It reads locally
through the existing Speech Dispatcher service and its supported output-module
API. The native Piper module keeps the model loaded across utterances. The
voice manager discovers the real installed voice and plays an audible sample.
There is no runtime downloader, extra speech service or browser-backed engine.

%prep
%autosetup -n luma-natural-voices
mkdir piper speechd
tar -xf %{SOURCE1} --strip-components=1 -C piper
tar -xf %{SOURCE2} --strip-components=1 -C speechd
patch --batch --forward --fuzz=0 -p1 -d piper < system-piper-threads.patch
%{python3} add-native-module.py speechd .

%build
cmake -S . -B piper-build -DPIPER_SOURCE_DIR=$PWD/piper/libpiper \
 -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX=%{_prefix} \
 -DCMAKE_INSTALL_LIBDIR=%{_lib} -DCMAKE_CXX_FLAGS='%{optflags}'
cmake --build piper-build --parallel 2
pushd speechd
autoreconf -fi
./configure --prefix=%{_prefix} --libdir=%{_libdir} --sysconfdir=%{_sysconfdir} \
 --with-module-bindir=%{_libdir}/speech-dispatcher-modules --disable-ltdl \
 --with-espeak=no --with-espeak-ng=no --with-pulse=no --with-alsa=no \
 --with-libao=no --with-pipewire=no --with-nas=no --with-oss=no \
 --with-systemdsystemunitdir=no --with-systemduserunitdir=no
make -C src/common -j2
make -C src/audio -j2
make -C src/modules -j2 sd_piper \
 PIPER_CFLAGS="-I$PWD/../piper/libpiper/include" \
 PIPER_LIBS="-L$PWD/../piper-build -lluma-piper -lrubberband"
popd
%{python3} -m py_compile luma_natural_voices/*.py

%install
DESTDIR=%{buildroot} cmake --install piper-build
# Only the new module is packaged. The existing server, basic voices, audio
# adapters, client libraries and user service remain owned by Fedora.
install -Dm0755 speechd/src/modules/.libs/sd_piper \
 %{buildroot}%{_libdir}/speech-dispatcher-modules/sd_piper
install -Dm0644 data/piper.conf %{buildroot}%{_sysconfdir}/speech-dispatcher/modules/piper.conf
# Fedora explicitly requests espeak-ng, disabling automatic module discovery.
# Its existing global Include clients/*.conf admits this owned AddModule row.
install -Dm0644 data/luma-piper.conf %{buildroot}%{_sysconfdir}/speech-dispatcher/clients/luma-piper.conf
install -Dm0644 %{SOURCE3} %{buildroot}%{_datadir}/luma-voices/en_US-ljspeech-medium.onnx
install -Dm0644 %{SOURCE4} %{buildroot}%{_datadir}/luma-voices/en_US-ljspeech-medium.onnx.json
install -d %{buildroot}%{python3_sitelib}/luma_natural_voices
install -m0644 luma_natural_voices/*.py %{buildroot}%{python3_sitelib}/luma_natural_voices/
install -Dm0755 data/org.projectluma.NaturalVoices.in %{buildroot}%{_bindir}/org.projectluma.NaturalVoices
install -Dm0644 data/org.projectluma.NaturalVoices.desktop %{buildroot}%{_datadir}/applications/org.projectluma.NaturalVoices.desktop
install -Dm0644 data/org.projectluma.NaturalVoices.metainfo.xml %{buildroot}%{_metainfodir}/org.projectluma.NaturalVoices.metainfo.xml
# Only the versioned private component library is a runtime payload.
rm -f %{buildroot}%{_libdir}/libluma-piper.so

%check
LD_LIBRARY_PATH=$PWD/piper-build ./piper-build/synthesis-check %{SOURCE3} %{SOURCE4}
%{python3} tests/module_protocol.py speechd/src/modules/.libs/sd_piper %{SOURCE3} %{SOURCE4} piper-build
timeout --kill-after=5s 45s dbus-run-session -- xvfb-run -a env GSK_RENDERER=cairo GTK_A11Y=none PYTHONPATH=$PWD %{python3} tests/runtime_manager.py
desktop-file-validate data/org.projectluma.NaturalVoices.desktop
appstream-util validate-relax --nonet data/org.projectluma.NaturalVoices.metainfo.xml

%files
%doc README.md
%license LICENSE.md VOICE-LICENSE.md piper/COPYING
%license speechd/src/modules/module_main.c piper/libpiper/include/uni_algo.h piper/libpiper/include/json.hpp
%license speechd/COPYING.LGPL speechd/COPYING.GPL-2 speechd/COPYING.GPL-3
%{_libdir}/libluma-piper.so.1*
%{_libdir}/speech-dispatcher-modules/sd_piper
%config(noreplace) %{_sysconfdir}/speech-dispatcher/modules/piper.conf
%config(noreplace) %{_sysconfdir}/speech-dispatcher/clients/luma-piper.conf
%{_datadir}/luma-voices/
%{_bindir}/org.projectluma.NaturalVoices
%{python3_sitelib}/luma_natural_voices/
%{_datadir}/applications/org.projectluma.NaturalVoices.desktop
%{_metainfodir}/org.projectluma.NaturalVoices.metainfo.xml

%changelog
* Tue Oct 06 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.4.creator20261007.1
- Wait for actual native playback termination before allowing another sample.
- Ignore stale speech events from earlier playback generations.
- Avoid the AppWindow state attribute collision in the native manager.
- Qualify actual ordinary-user GTK manager startup and worker cleanup.
- Register Piper through the installed Speech Dispatcher configuration include.
- Preserve the existing server and default basic voice; native discovery is checked separately.
