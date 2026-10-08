# SPDX-License-Identifier: MPL-2.0
# Luma Messages end-to-end encryption (ADR-051): the luma-mls crate's desktop
# helper. Messages runs it as the "Luma" account; without it installed Messages
# simply does not offer one, so neither package requires the other.
%global debug_package %{nil}

Name:           luma-messages-e2ee
Version:        0.1.0
Release:        1.luma.2.creator20261007.1%{?dist}
Summary:        End-to-end encrypted Luma Messages between Luma devices
# luma-mls and the helper: MPL-2.0. Vendored crates (Cargo.lock): OpenMLS and
# RustCrypto (MIT OR Apache-2.0), ICU data (Unicode-3.0), and the rest below.
License:        MPL-2.0 AND (MIT OR Apache-2.0) AND MIT AND Apache-2.0 AND Unicode-3.0 AND BSD-3-Clause AND (Unlicense OR MIT) AND (BSD-2-Clause OR Apache-2.0 OR MIT)
URL:            https://project-luma.local/messages
ExclusiveArch:  x86_64 aarch64
# scripts/packages/build-luma-messages-e2ee-source.sh: the crate with every
# dependency vendored against Cargo.lock, so the build needs no network.
Source0:        luma-messages-e2ee.tar.gz

BuildRequires:  cargo >= 1.85
BuildRequires:  rust >= 1.85
BuildRequires:  gcc
BuildRequires:  openssl-devel
BuildRequires:  python3

%description
The helper Luma Messages uses for conversations between Luma accounts. Every
message and photo is encrypted on the device with MLS (RFC 9420) and can only
be read by the devices in the conversation; Luma's servers carry ciphertext.
It speaks luma-messages-bridge/1 to Messages and keeps its keys in a file
sealed with a key held in the keyring.

%prep
%autosetup -n luma-messages-e2ee

%build
export CARGO_HOME=$PWD/.cargo-home
cargo build --release --offline --locked --bin luma

%install
install -D -m 0755 target/release/luma %{buildroot}%{_libexecdir}/luma-messages/luma
# Licence texts of every vendored crate, by crate.
(cd vendor && find . -mindepth 2 -maxdepth 2 -type f \( -iname 'LICENSE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' -o -iname 'UNLICENSE*' \) -print0 |
  while IFS= read -r -d '' file; do install -D -m 0644 "$file" "../licenses/vendor/$file"; done)
found=$(find licenses/vendor -type f | wc -l)
[ "$found" -gt 50 ] || { echo "only $found vendored licence files were found" >&2; exit 1; }

%check
export CARGO_HOME=$PWD/.cargo-home
# The library alone, without the helper's networking: the surface an Android
# (UniFFI) or web (wasm32) client builds on.
cargo check --offline --locked --lib --no-default-features
# Every test binary must run tests; "0 passed" from a runner that reached
# nothing is a failure here. Library 6, engine 9, helper 5.
cargo test --release --offline --locked 2>&1 | tee cargo-test.log
passed=$(sed -n 's/^test result: ok\. \([0-9]*\) passed.*/\1/p' cargo-test.log | awk '{s+=$1} END {print s+0}')
failed=$(grep -c '^test result: FAILED' cargo-test.log || true)
[ "$failed" -eq 0 ] && [ "$passed" -ge 20 ] || { echo "cargo test: $passed passed, $failed failed runs; expected at least 20" >&2; exit 1; }
# The revocation test seen red: with the removal skipped it must fail.
if LUMA_MLS_BREAK=keep-revoked cargo test --release --offline --locked --test engine a_removed_device 2>&1 | grep -q 'REVOKED DEVICE READ'; then
  echo "revocation test goes red when the device is left in the group"
else
  echo "the revocation test did not fail with the removal skipped" >&2; exit 1
fi
# End to end through the local fake Hub, then each check seen red.
python3 tests/e2e.py --helper target/release/luma
python3 tests/e2e.py --helper target/release/luma --self-test

%files
%license LICENSE licenses
%doc README.md
%dir %{_libexecdir}/luma-messages
%{_libexecdir}/luma-messages/luma

%changelog
* Wed Sep 23 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1
- First release: luma-mls on OpenMLS 0.6.0 (ciphersuite 0x0003) and the luma helper for Messages, against the Luma Messages delivery API v1
- Device identity and keys sealed with a keyring-held key in one file written atomically; a message is acknowledged only after the state that applied it is on disk
- 1:1 and group conversations as MLS groups; devices added and revoked devices removed automatically; daily key updates
- Photos encrypted per file with AES-256-GCM; safety numbers with QR verification; requests, blocks and reports
