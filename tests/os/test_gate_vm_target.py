#!/usr/bin/python3
"""Exercise real gate target selection and libvirt capability admission."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'scripts/os/lib/gate-vm.sh'

class GateVM(unittest.TestCase):
    def run_helper(self, arch='aarch64', host='x86_64', kvm='yes', extra='', env=None):
        return subprocess.run(['bash', '-c', '''set -euo pipefail
luma_os_die() { echo "$*" >&2; return 1; }
. "$1"
LUMA_OS_ARCH=$2
luma_os_gate_vm_target "$2" "$3" "$4"
printf '%s|%s|%s|%s|%s\\n' "$gate_machine" "$gate_console" "$gate_cpu" "$gate_virt" "$gate_boot"
''' + extra, 'gate-test', str(HELPER), arch, host, kvm],
            env={**os.environ, **(env or {})}, text=True, capture_output=True)

    def test_cross_arm_uses_tcg_and_arm_console(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'virt|ttyAMA0|max|qemu|uefi')

    def test_native_arm_uses_kvm(self):
        result = self.run_helper(host='aarch64')
        self.assertEqual(result.stdout.strip(), 'virt|ttyAMA0|host-passthrough|kvm|uefi')

    def test_native_x86_without_kvm_uses_tcg(self):
        result = self.run_helper(arch='x86_64', kvm='no')
        self.assertEqual(result.stdout.strip(), 'q35|ttyS0|max|qemu|uefi')

    def test_install_wait_matches_emulation_profile(self):
        for arch, host, kvm, expected in (
                ('aarch64', 'x86_64', 'yes', '180'),
                ('aarch64', 'x86_64', 'no', '180'),
                ('aarch64', 'aarch64', 'yes', '60'),
                ('aarch64', 'aarch64', 'no', '60'),
                ('x86_64', 'x86_64', 'yes', '60'),
                ('x86_64', 'x86_64', 'no', '60'),
                ('x86_64', 'aarch64', 'no', '60')):
            with self.subTest(arch=arch, host=host, kvm=kvm):
                result = self.run_helper(arch=arch, host=host, kvm=kvm,
                    extra='printf "wait=%s\\n" "$gate_install_wait_minutes"')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines()[-1], 'wait=' + expected)

    def test_unknown_architecture_refused(self):
        result = self.run_helper(arch='riscv64')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unsupported gate architecture', result.stderr)

    def fixture(self, firmware=True, arch='aarch64', explicit=False):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        binary = base / 'virsh'
        binary.write_text('#!/bin/sh\ncase "$*" in *domcapabilities*) cat "$FIXTURE_CAPS";; *) cat "$FIXTURE_HOST";; esac\n')
        binary.chmod(0o755)
        caps = base / 'caps.xml'
        caps.write_text(f'<domainCapabilities><arch>{arch}</arch><domain>qemu</domain>'
                        '<os><enum name="firmware">' + ('<value>efi</value>' if firmware else '') +
                        '</enum></os></domainCapabilities>')
        host_caps = base / 'host.xml'
        host_caps.write_text('<capabilities><guest><os_type>hvm</os_type><arch name="aarch64">'
                             '<domain type="qemu"/></arch></guest></capabilities>')
        env = {'PATH': str(base) + ':' + os.environ['PATH'], 'FIXTURE_CAPS': str(caps),
               'FIXTURE_HOST': str(host_caps)}
        if explicit:
            env.update(LUMA_OS_GATE_EMULATOR=str(binary),
                       LUMA_OS_GATE_UEFI_CODE=str(caps), LUMA_OS_GATE_UEFI_VARS=str(caps))
        return base, env

    def test_efi_capabilities_admitted(self):
        base, env = self.fixture()
        result = self.run_helper(extra='luma_os_gate_vm_preflight "$FIXTURE_CAPS.out"', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((base / 'caps.xml.out').is_file())

    def test_explicit_emulator_cannot_bypass_missing_host_capabilities(self):
        base, env = self.fixture(firmware=False, explicit=True)
        (base / 'host.xml').write_text('<capabilities/>')
        result = self.run_helper(extra='luma_os_gate_vm_preflight "$FIXTURE_CAPS.out"', env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('host capabilities do not advertise', result.stderr)

    def test_missing_efi_refused(self):
        _, env = self.fixture(firmware=False)
        result = self.run_helper(extra='luma_os_gate_vm_preflight "$FIXTURE_CAPS.out"', env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('UEFI firmware is unavailable', result.stderr)

    def test_wrong_architecture_refused(self):
        _, env = self.fixture(arch='x86_64')
        result = self.run_helper(extra='luma_os_gate_vm_preflight "$FIXTURE_CAPS.out"', env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('do not match', result.stderr)

    def test_explicit_firmware_admitted_and_hashed(self):
        base, env = self.fixture(firmware=False, explicit=True)
        result = self.run_helper(extra='luma_os_gate_vm_preflight "$FIXTURE_CAPS.out"', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('loader.readonly=yes,loader.type=pflash,nvram.template=', result.stdout)
        self.assertEqual(len((base / 'caps.xml.out.inputs.sha256').read_text().splitlines()), 3)

    def test_partial_firmware_refused(self):
        _, env = self.fixture(explicit=True)
        del env['LUMA_OS_GATE_UEFI_VARS']
        result = self.run_helper(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('must both be readable', result.stderr)

    def test_missing_emulator_refused(self):
        result = self.run_helper(env={'LUMA_OS_GATE_EMULATOR': '/nonexistent/qemu'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('executable absolute path', result.stderr)

if __name__ == '__main__':
    unittest.main()
