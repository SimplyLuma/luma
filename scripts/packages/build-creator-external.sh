#!/usr/bin/env bash
# Build source-pinned creator applications with their normal RPM checks.
set -euo pipefail
app=${1:?application required}
source_root=${2:?source tree required}
work=${3:?unique writable work directory required}
platform=${4:?current platform RPM required}
platform_devel=${5:?current platform devel RPM required}
case "$app" in
  grid) package=luma-grid-preview; top=LumaGrid-0.1.0; archive=LumaGrid-0.1.0.tar.xz ;;
  stage) package=luma-stage-preview; top=Stage-0.1.0; archive=Stage-0.1.0.tar.xz ;;
  session) package=luma-session; top=luma-session-0.1.0; archive=luma-session-0.1.0.tar.gz ;;
  canvas) package=luma-canvas-preview; top=luma-canvas; archive=luma-canvas.tar.gz ;;
  write) package=luma-write; top=luma-write; archive=luma-write.tar.gz ;;
  charlie) package=luma-charlie; top=charlie-luma; archive=charlie-luma.tar.gz ;;
  *) echo "unsupported application: $app" >&2; exit 2 ;;
esac
mkdir -p "$work"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
cp "$source_root/packaging/rpm/$package.spec" "$work/SPECS/"
case "$archive" in
  *.xz) tar -C "$source_root/src/external/$app" --exclude=.git --exclude=__pycache__ --exclude='*.pyc' --transform="s,^\\.,$top," -cJf "$work/SOURCES/$archive" . ;;
  *.gz) tar -C "$source_root/src/external/$app" --exclude=.git --exclude=__pycache__ --exclude='*.pyc' --transform="s,^\\.,$top," -czf "$work/SOURCES/$archive" . ;;
esac
if [[ $app == charlie ]]; then
  install -m0644 "$source_root/LICENSE.md" "$work/SOURCES/LICENSE.md"
  install -m0644 "$source_root/src/luma-shell-state/org.project_luma.shell-state.gschema.xml" "$work/SOURCES/"
fi
# Use dnf rather than rpm --nodeps: dependency checks remain active.
sdk=("$(dirname "$platform")"/luma-developer-platform-sdk-*.rpm)
if [[ ${#sdk[@]} == 1 && -f ${sdk[0]} ]]; then
  dnf -y install "$platform" "$platform_devel" "${sdk[0]}"
  dnf -y reinstall "$platform" "$platform_devel" "${sdk[0]}"
else
  dnf -y install "$platform" "$platform_devel"
  dnf -y reinstall "$platform" "$platform_devel"
fi
args=(--define "_topdir $work" --define '_smp_mflags -j2')
if [[ $app == session ]]; then
  evr=$(rpm -qp --qf '%{VERSION}-%{RELEASE}' "$platform")
  args+=(--define "platform_version ${evr%.fc44}")
fi
dnf -y builddep "${args[@]}" "$work/SPECS/$package.spec"
if [[ ( $app == grid || $app == charlie ) && $(id -u) == 0 ]]; then
  # Calc's real read-only test must exercise ordinary file permissions.
  # Root can write a 0444 fixture and would invalidate that contract.
  # Charlie's real HTML reader must likewise run as a normal desktop user:
  # WebKit's native sandbox cannot be evaluated as root in an RPM builder.
  if ! id conform >/dev/null 2>&1; then
    useradd --system --create-home --shell /bin/bash conform
  fi
  chown -R conform:conform "$work"
  install -d -o conform -g conform /work/dialog-evidence
  test_home=$(mktemp -d "$work/check-home.XXXXXX")
  install -d -o conform -g conform -m 0700 "$test_home" "$test_home/runtime"
  install -d -o conform -g conform "$test_home/config" "$test_home/data" "$test_home/cache" "$test_home/state"
  runuser -u conform -- env HOME="$test_home" XDG_RUNTIME_DIR="$test_home/runtime" \
    XDG_CONFIG_HOME="$test_home/config" XDG_DATA_HOME="$test_home/data" \
    XDG_CACHE_HOME="$test_home/cache" XDG_STATE_HOME="$test_home/state" \
    GIO_USE_VFS=local GDK_BACKEND=x11 rpmbuild -ba "${args[@]}" "$work/SPECS/$package.spec"
else
  install -d -m 0700 "$work/test-runtime"
  XDG_RUNTIME_DIR="$work/test-runtime" rpmbuild -ba "${args[@]}" "$work/SPECS/$package.spec"
fi
test "$(find "$work/RPMS" -name "$package-*.rpm" | wc -l)" -ge 1
find "$work/RPMS" -name '*.rpm' -print0 | sort -z | xargs -0 sha256sum > "$work/SHA256SUMS"
