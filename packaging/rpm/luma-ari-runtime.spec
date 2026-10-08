# SPDX-License-Identifier: Apache-2.0
# Ari's local model runtime: llama.cpp's llama-server, pinned, with the Vulkan
# backend for integrated and discrete GPUs and every CPU variant as fallback.
# Installed privately under libexec so it never collides with Fedora's llama-cpp.

%global llama_version 0.4.0
%global privlib %{_libdir}/luma-ari

Name:           luma-ari-runtime
Version:        %{llama_version}
Release:        1.luma.1%{?dist}
Summary:        Local model runtime for Ari, Luma's assistant (llama.cpp)
License:        MIT
URL:            https://github.com/ggml-org/llama.cpp
Source0:        https://codeload.github.com/ggml-org/llama.cpp/tar.gz/refs/tags/v%{llama_version}#/llama.cpp-%{llama_version}.tar.gz

BuildRequires:  cmake >= 3.21
BuildRequires:  gcc-c++
BuildRequires:  ninja-build
BuildRequires:  vulkan-headers
BuildRequires:  vulkan-loader-devel
BuildRequires:  glslc
BuildRequires:  glslang
BuildRequires:  spirv-headers-devel
BuildRequires:  openssl-devel
BuildRequires:  chrpath
Requires:       vulkan-loader
Recommends:     mesa-vulkan-drivers

%description
The llama.cpp inference server Ari starts as a child process to run GGUF models
on this machine. The Vulkan backend uses the machine's GPU where one is
available; CPU backends for every x86-64 feature level are loaded otherwise.
Nothing here downloads a model; Ari does that only after the person agrees.

%prep
%autosetup -n llama.cpp-%{llama_version}

%build
%cmake -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=ON \
  -DGGML_BACKEND_DL=ON \
  -DGGML_BACKEND_DIR=%{privlib} \
  -DGGML_CPU_ALL_VARIANTS=ON \
  -DGGML_NATIVE=OFF \
  -DGGML_VULKAN=ON \
  -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_SERVER=ON \
  -DCMAKE_INSTALL_RPATH=%{privlib} \
  -DCMAKE_SKIP_INSTALL_RPATH=OFF
%cmake_build --target llama-server llama-bench

%install
install -d %{buildroot}%{_libexecdir}/luma-ari %{buildroot}%{privlib}
install -m 0755 %{__cmake_builddir}/bin/llama-server %{buildroot}%{_libexecdir}/luma-ari/llama-server
install -m 0755 %{__cmake_builddir}/bin/llama-bench %{buildroot}%{_libexecdir}/luma-ari/llama-bench
find %{__cmake_builddir}/bin -maxdepth 1 -name '*.so*' -exec install -m 0755 {} %{buildroot}%{privlib}/ \;
# Point every binary and backend at the private library directory, never the build tree.
for f in %{buildroot}%{_libexecdir}/luma-ari/* %{buildroot}%{privlib}/*.so*; do
  [ -L "$f" ] || chrpath -r %{privlib} "$f" >/dev/null 2>&1 || chrpath -d "$f" >/dev/null 2>&1 || :
done

%check
LD_LIBRARY_PATH=%{buildroot}%{privlib} %{buildroot}%{_libexecdir}/luma-ari/llama-server --version

%files
%license LICENSE
%{_libexecdir}/luma-ari/
%{privlib}/

%changelog
* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.4.0-1.luma.1
- llama.cpp 0.4.0 llama-server with Vulkan and all CPU backend variants for Ari
