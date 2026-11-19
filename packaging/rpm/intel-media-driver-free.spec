# Fedora's intel-media-driver-free 26.3.5-1.fc44 spec (rpmautospec expanded)
# with Luma's release and the bcond below. Sources come from Fedora's source
# RPM (scripts/packages/build-intel-media-driver-free.sh pins its digest): the
# same "free" tarball, with the prebuilt non-free EU kernels already removed.
# Rebase on Fedora's spec when Fedora updates the package.

# VDEnc H.264 and H.265 encode (fixed-function, Gen12 and later). Fedora builds
# these off; the code is in the free tarball and needs no EU kernels. VME
# encode stays off (it runs on the removed kernels), and so do H.264/H.265/VC-1
# /VVC decode.
%bcond va_h26x_encode 1

Name:       intel-media-driver-free
Version:    26.3.5
Release:    1.luma.1%{?dist}
Summary:    The Intel Media Driver for VAAPI
License:    MIT and BSD
URL:        https://github.com/intel/media-driver

# Original source has non-free and/or patented files, use following on the original source!
# $ python3 strip.py
# ref. https://github.com/intel/media-driver/wiki/Media-Driver-Shaders-(EU-Kernels)#build-with-open-source-shaders

#Source0:    %%{url}/archive/intel-media-%%{version}%%{?pre}.tar.gz
Source0:    intel-media-%{version}-free.tar.gz
Source1:    intel-media-driver.metainfo.xml
Source2:    strip.py

# This is an Intel only vaapi backend
ExclusiveArch:  i686 x86_64

BuildRequires:  cmake
BuildRequires:  gcc
BuildRequires:  gcc-c++

# AppStream metadata generation
BuildRequires:  libappstream-glib

# Enable once we start building free kernels
#BuildRequires:  intel-igc-devel
#BuildRequires:  intel-compute-runtime

BuildRequires:  pkgconfig(libcmrt)
BuildRequires:  pkgconfig(igdgmm)
BuildRequires:  python3
BuildRequires:  python3-devel
BuildRequires:  python-unversioned-command
BuildRequires:  libva-devel
BuildRequires:  libpciaccess-devel
BuildRequires:  libdrm-devel
BuildRequires:  pkgconfig(x11)
BuildRequires:  ninja-build

%description
The Intel Media Driver for VAAPI is a new VA-API (Video Acceleration API)
user mode driver supporting hardware accelerated decoding, encoding,
and video post processing for GEN based graphics hardware.
https://github.com/intel/media-driver/wiki

%package -n     libva-intel-media-driver
Summary:        The Intel Media Driver for VAAPI.

%description -n libva-intel-media-driver
The Intel Media Driver for VAAPI is a new VA-API (Video Acceleration API)
user mode driver supporting hardware accelerated decoding, encoding,
and video post processing for GEN based graphics hardware.
https://github.com/intel/media-driver/wiki

%package -n     libigfxcmrt
Summary:        Library to load own GPU kernels on render engine via Intel media driver.
Requires:       libva-intel-media-driver%{?_isa} = %{version}-%{release}

%description -n libigfxcmrt
libigfxcmrt is a runtime library needed when user wants to execute their own GPU kernels on render engine.
It calls Intel media driver to load the kernels and allocate the resources.
It provides a set of APIs for user to call directly from application.

%package -n     libigfxcmrt-devel
Summary:        Development files for libigfxcmrt
Requires:       libigfxcmrt%{?_isa} = %{version}-%{release}

%description -n libigfxcmrt-devel
The libigfxcmrt-devel package contains libraries and header files for
developing applications that use libigfxcmrt.

%prep
%autosetup -p1 -n media-driver-intel-media-%{version}%{?pre}
# Fix license perm
chmod -x LICENSE.md README.md CMakeLists.txt

# Remove pre-built (but unused) files
rm -rv Tools/MediaDriverTools/UMDPerfProfiler/MediaPerfParser

%build
# TODO: Please submit an issue to upstream (rhbz#2380654)
export CMAKE_POLICY_VERSION_MINIMUM=3.5
%ifarch %{ix86}
export CXXFLAGS="%{optflags} -D_FILE_OFFSET_BITS=64"
%endif
%cmake \
%ifarch %{ix86}
  -DARCH:STRING=32 \
%endif
  -DENABLE_NONFREE_KERNELS:BOOL=OFF \
  -DENABLE_KERNELS:BOOL=OFF \
  -DBUILD_KERNELS:BOOL=OFF \
  -DLIBVA_DRIVERS_PATH=%{_libdir}/dri \
  -DBUILD_CMRTLIB=ON \
  -DMEDIA_BUILD_FATAL_WARNINGS=OFF \
  -DVC1_Decode_Supported="no" \
  -DAVC_Encode_VME_Supported="no" \
%if %{with va_h26x_encode}
  -DAVC_Encode_VDEnc_Supported="yes" \
%else
  -DAVC_Encode_VDEnc_Supported="no" \
%endif
  -DHEVC_Encode_VME_Supported="no" \
%if %{with va_h26x_encode}
  -DHEVC_Encode_VDEnc_Supported="yes" \
%else
  -DHEVC_Encode_VDEnc_Supported="no" \
%endif
  -DAVC_Decode_Supported="no" \
  -DHEVC_Decode_Supported="no" \
  -DAVC_Decode_Supported="no" \
  -DVVC_Decode_Supported="no" \
  -G Ninja

%cmake_build

%install
%cmake_install

# Fix perm on library to be stripped
chmod +x %{buildroot}%{_libdir}/dri/iHD_drv_video.so

# install AppData and add modalias provides
mkdir -p %{buildroot}%{_metainfodir}
install -pm 0644 %{SOURCE1} %{buildroot}%{_metainfodir}/%{name}.metainfo.xml

# TODO - have pci based hw detection
%if 0
fn=%{buildroot}%{_metainfodir}/intel-media-driver.metainfo.xml
%{SOURCE9} src/i965_pciids.h | xargs appstream-util add-provide ${fn} modalias
%endif

%files -n libva-intel-media-driver
%doc README.md
%license LICENSE.md
%{_libdir}/dri/iHD_drv_video.so
%{_metainfodir}/%{name}.metainfo.xml

%files -n libigfxcmrt
%{_libdir}/libigfxcmrt.so.*

%files -n libigfxcmrt-devel
%{_libdir}/libigfxcmrt.so
%{_includedir}/igfxcmrt
%{_libdir}/pkgconfig/igfxcmrt.pc


%changelog
* Thu Nov 19 2026 Project Luma <maintainers@projectluma.org> - 26.3.5-1.luma.1
- Enable VDEnc H.264 and H.265 encode (bcond va_h26x_encode).

## START: Generated by rpmautospec
* Tue Oct 06 2026 František Zatloukal <fzatlouk@redhat.com> - 26.3.5-1
- intel-media-26.3.5

* Sun Aug 02 2026 František Zatloukal <fzatlouk@redhat.com> - 26.2.4-1
- intel-media-26.2.4

* Thu Jul 16 2026 Fedora Release Engineering <releng@fedoraproject.org> - 26.1.5-3
- Rebuilt for https://fedoraproject.org/wiki/Fedora_45_Mass_Rebuild

* Wed May 27 2026 Androniychuk, Pavel <pavel.androniychuk@intel.com> - 26.1.5-2
- Update old links

* Sat May 02 2026 František Zatloukal <fzatlouk@redhat.com> - 26.1.5-1
- intel-media-26.1.5

* Mon Feb 16 2026 Cristian Le <git@lecris.dev> - 25.4.6-3
- Allow to build with CMake 4.0 (rhbz#2380654)

* Fri Jan 16 2026 Fedora Release Engineering <releng@fedoraproject.org> - 25.4.6-2
- Rebuilt for https://fedoraproject.org/wiki/Fedora_44_Mass_Rebuild

* Thu Dec 25 2025 František Zatloukal <fzatlouk@redhat.com> - 25.4.6-1
- intel-media-25.4.6

* Tue Sep 23 2025 František Zatloukal <fzatlouk@redhat.com> - 25.3.4-1
- intel-media-25.3.4

* Wed Jul 30 2025 Androniychuk, Pavel <pavel.androniychuk@intel.com> - 25.2.6-3
- Turn cmrtlib ON

* Thu Jul 24 2025 Fedora Release Engineering <releng@fedoraproject.org> - 25.2.6-2
- Rebuilt for https://fedoraproject.org/wiki/Fedora_43_Mass_Rebuild

* Fri Jul 04 2025 František Zatloukal <fzatlouk@redhat.com> - 25.2.6-1
- intel-media-25.2.6

* Sun Mar 23 2025 František Zatloukal <fzatlouk@redhat.com> - 25.1.4-1
- intel-media-25.1.4

* Mon Jan 20 2025 František Zatloukal <fzatlouk@redhat.com> - 24.4.4-3
- Fix GCC 15 Build, fix description (RHBZ#2334580)

* Fri Jan 17 2025 Fedora Release Engineering <releng@fedoraproject.org> - 24.4.4-2
- Rebuilt for https://fedoraproject.org/wiki/Fedora_42_Mass_Rebuild

* Sun Dec 22 2024 František Zatloukal <fzatlouk@redhat.com> - 24.4.4-1
- intel-media-24.4.4

* Mon Sep 23 2024 František Zatloukal <fzatlouk@redhat.com> - 24.3.4-1
- intel-media-24.3.4

* Mon Jul 22 2024 Frantisek Zatloukal <Zatloukal.Frantisek@gmail.com> - 24.2.5-1
- intel-media-24.2.5

* Thu Jul 18 2024 Fedora Release Engineering <releng@fedoraproject.org> - 24.1.5-2
- Rebuilt for https://fedoraproject.org/wiki/Fedora_41_Mass_Rebuild

* Wed Mar 20 2024 Frantisek Zatloukal <Zatloukal.Frantisek@gmail.com> - 24.1.5-1
- intel-media-24.1.5

* Mon Mar 18 2024 Frantisek Zatloukal <Zatloukal.Frantisek@gmail.com> - 24.1.3-4
- Manually set LIBVA_DRIVERS_PATH to the default single path

* Tue Feb 27 2024 Nicolas Chauvet <kwizart@gmail.com> - 24.1.3-3
- Rename metainfo to match package name

* Tue Feb 27 2024 Nicolas Chauvet <kwizart@gmail.com> - 24.1.3-2
- Drop conflicts with intel-media-driver

* Fri Feb 02 2024 Frantisek Zatloukal <Zatloukal.Frantisek@gmail.com> - 24.1.3-1
- intel-media-24.1.3

* Mon Jan 29 2024 Frantisek Zatloukal <Zatloukal.Frantisek@gmail.com> - 24.1.2-1
- intel-media-24.1.2

* Wed Jan 24 2024 Fedora Release Engineering <releng@fedoraproject.org> - 23.4.3-4
- Rebuilt for https://fedoraproject.org/wiki/Fedora_40_Mass_Rebuild

* Sat Jan 20 2024 Fedora Release Engineering <releng@fedoraproject.org> - 23.4.3-3
- Rebuilt for https://fedoraproject.org/wiki/Fedora_40_Mass_Rebuild

* Tue Jan 16 2024 Frantisek Zatloukal <Zatloukal.Frantisek@gmail.com> - 23.4.3-2
- Update appstream metainfo

* Mon Dec 11 2023 Frantisek Zatloukal <Zatloukal.Frantisek@gmail.com> - 23.4.3-1
- intel-media-23.4.3

* Thu Dec 07 2023 Frantisek Zatloukal <Zatloukal.Frantisek@gmail.com> - 23.4.2-3
- Fix libigfxcmrt requires (Fixes RHBZ#2253145)

* Wed Dec 06 2023 Frantisek Zatloukal <Zatloukal.Frantisek@gmail.com> - 23.4.2-1
- Initial package, taken from rpmfusion and reduced to contain only free parts

## END: Generated by rpmautospec
