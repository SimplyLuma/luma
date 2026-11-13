# SPDX-License-Identifier: Apache-2.0
"""Every service may log when PID 1 labels the journal socket syslogd_t.

Phones boot through a busybox initramfs, so PID 1 loads the policy before it
creates /run/systemd/journal/socket and the socket carries syslogd_t instead
of the kernel_t a systemd initramfs leaves on it. luma_journal.cil lets the
domains Fedora allows only kernel_t send to syslogd_t.

LUMA_SELINUX_POLICY=<compiled policy.NN> (with SETools installed) also checks
that policy: every domain that may send to a kernel_t journal socket may send
to a syslogd_t one, through Fedora's rules or luma_journal.cil.
"""
import os
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
CIL = ROOT / 'config/desktop/selinux/luma_journal.cil'
CONFIGURE = ROOT / 'image/luma-desktop/scripts/configure-system.sh'
TMPFILES = ROOT / 'config/desktop/luma-selinux-run.conf'
HOMEDIRS_UNIT = ROOT / 'config/desktop/luma-selinux-homedirs.service'
HOMEDIRS = ROOT / 'config/desktop/luma-selinux-homedirs'
OBSERVED = {'systemd_nsresourced_t', 'iiosensorproxy_t', 'systemd_hwdb_t', 'systemd_oomd_t'}
RULE = re.compile(r'^\(optional (luma_journal_\w+) \(allow (\w+_t) syslogd_t \(unix_dgram_socket \(sendto\)\)\)\)$')


def cil_domains():
    names, domains = set(), []
    for line in CIL.read_text().splitlines():
        if not line or line.startswith(';'):
            continue
        match = RULE.match(line)
        if not match:
            raise AssertionError(f'luma_journal.cil: not a single journal sendto rule: {line}')
        name, domain = match.groups()
        if name != 'luma_journal_' + domain.removesuffix('_t') or name in names:
            raise AssertionError(f'luma_journal.cil: optional block name {name} for {domain}')
        names.add(name)
        domains.append(domain)
    return domains


class ImageTests(unittest.TestCase):
    def test_module_grants_only_journal_sendto(self):
        domains = cil_domains()
        self.assertEqual(domains, sorted(set(domains)))
        self.assertLessEqual(OBSERVED, set(domains))

    def test_image_compiles_the_module_and_restores_default_hostname(self):
        script = CONFIGURE.read_text()
        self.assertRegex(script, r'semodule -i [^\n]*config/desktop/selinux/luma_journal\.cil')
        self.assertIn('config/desktop/luma-selinux-run.conf" /usr/lib/tmpfiles.d/luma-selinux-run.conf', script)
        lines = [l for l in TMPFILES.read_text().splitlines() if l and not l.startswith('#')]
        self.assertEqual(lines, ['z /run/systemd/default-hostname - - - -'])

    def test_var_home_is_labeled_before_homed_watches_it(self):
        unit = HOMEDIRS_UNIT.read_text()
        after = re.search(r'^After=(.*)$', unit, re.M).group(1).split()
        before = re.search(r'^Before=(.*)$', unit, re.M).group(1).split()
        self.assertIn('systemd-tmpfiles-setup.service', after)
        self.assertIn('systemd-homed.service', before)
        script = HOMEDIRS.read_text()
        early = script.index("grep -q '^/var/home'")
        self.assertIn('restorecon /var/home\n  exit 0', script[early:script.index('defaults=$(mktemp)')])


@unittest.skipUnless(os.environ.get('LUMA_SELINUX_POLICY'), 'LUMA_SELINUX_POLICY names no compiled policy')
class PolicyTests(unittest.TestCase):
    def test_every_kernel_t_journal_sender_may_send_to_syslogd_t(self):
        import setools  # noqa: PLC0415 - optional; only this check needs it
        policy = setools.SELinuxPolicy(os.environ['LUMA_SELINUX_POLICY'])

        def senders(target):
            found = set()
            query = setools.TERuleQuery(policy, ruletype=['allow'], target=target,
                                        tclass=['unix_dgram_socket'], perms=['sendto'])
            for rule in query.results():
                if str(rule.source) == 'unconfined_domain_type':
                    continue
                found |= {str(t) for t in rule.source.expand()}
            return found

        missing = senders('kernel_t') - senders('syslogd_t') - set(cil_domains())
        self.assertEqual(sorted(missing), [], 'add these domains to config/desktop/selinux/luma_journal.cil')


if __name__ == '__main__':
    unittest.main()
