# SPDX-License-Identifier: Apache-2.0
"""Carrier selection from SIM files: decoding, table precedence and APN fallback."""
import importlib.machinery, importlib.util, pathlib, sys, tempfile, unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[1] / 'scripts/mobile/luma-fp6-carrier-select'
TABLE = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else HERE.parents[1] / 'config/mobile/fp6-carriers/carriers.toml'
sys.argv = sys.argv[:1]
loader = importlib.machinery.SourceFileLoader('carrier_select', str(SCRIPT))
spec = importlib.util.spec_from_loader('carrier_select', loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
ROWS = m.load_table(TABLE)


def ef_imsi(imsi: str) -> bytes:
    digits = [9 if len(imsi) % 2 else 1] + [int(d) for d in imsi]
    if len(digits) % 2:
        digits.append(0xF)
    body = bytes(digits[i] | (digits[i + 1] << 4) for i in range(0, len(digits), 2))
    return bytes([len(body)]) + body + b'\xff' * (8 - len(body))


def identity(mcc, mnc, gid1='', spn=''):
    return {'mcc': mcc, 'mnc': mnc, 'gid1': gid1, 'spn': spn}


MBPI = '''<?xml version="1.0"?><serviceproviders format="2.0">
 <country code="de"><provider><name>Telekom</name><gsm><network-id mcc="262" mnc="01"/>
  <apn value="internet.telekom"><usage type="internet"/></apn><apn value="mms.t-d1.de"><usage type="mms"/></apn></gsm></provider></country>
 <country code="nl"><provider><name>A</name><gsm><network-id mcc="204" mnc="08"/><apn value="a.example"/></gsm></provider>
  <provider><name>B</name><gsm><network-id mcc="204" mnc="08"/><apn value="b.example"/></gsm></provider></country>
</serviceproviders>'''


class Decoding(unittest.TestCase):
    def test_imsi_round_trip(self):
        for imsi in ('310260123456789', '26201987654321'):
            self.assertEqual(m.decode_imsi(ef_imsi(imsi)), imsi)
        self.assertIsNone(m.decode_imsi(b''))
        self.assertIsNone(m.decode_imsi(b'\x08\x29'))

    def test_mnc_length(self):
        self.assertEqual(m.mnc_length(bytes([0, 0, 0, 2]), '310'), 2)
        self.assertEqual(m.mnc_length(bytes([0, 0, 0, 3]), '262'), 3)
        self.assertEqual(m.mnc_length(None, '310'), 3)
        self.assertEqual(m.mnc_length(None, '262'), 2)

    def test_spn_and_gid1(self):
        self.assertEqual(m.decode_spn(b'\x01Tello' + b'\xff' * 11), 'Tello')
        self.assertEqual(m.decode_gid1(bytes.fromhex('6d38ffff')), '6D38')

    def test_identity_from_qmi_output_never_keeps_imsi(self):
        files = {'0x6F07': ef_imsi('310260123456789'), '0x6FAD': bytes([0, 0, 0, 3]),
                 '0x6F3E': b'\xff\xff', '0x6F46': b'\x01Tello' + b'\xff' * 11}
        def qmi(argument):
            raw = files[argument.rsplit(',', 1)[1]]
            return 'Card result:\n\tSW1: 0x90\nRead result:\n\t' + ':'.join(f'{b:02X}' for b in raw) + '\n'
        with mock.patch.object(m, 'qmi', side_effect=qmi):
            found = m.sim_identity()
        self.assertEqual(found, identity('310', '260', '', 'Tello'))
        self.assertNotIn('123456789', repr(found))


class Selection(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.mbpi = pathlib.Path(directory.name) / 'serviceproviders.xml'
        self.mbpi.write_text(MBPI)

    def decide(self, ident, att=False):
        return m.decide(ident, ROWS, att, self.mbpi)

    def test_tello_reference(self):
        # As read from a Tello eSIM: 310240, GID1 6941, no EF_SPN.
        d = self.decide(identity('310', '240', gid1='6941'))
        self.assertEqual((d['mcfg'], d['apn'], d['allowed_modes'], d['verified']), ('tmo-qrel1695', 'wholesale', '4g', True))

    def test_tmobile_network_without_mvno_match_uses_attach_apn(self):
        self.assertEqual(self.decide(identity('310', '240', gid1='FFFF'))['carrier'], 'T-Mobile US')
        d = self.decide(identity('310', '260', spn='Other'))
        self.assertEqual((d['carrier'], d['mcfg'], d['apn'], d['apn_source']), ('T-Mobile US', 'tmo-qrel1695', '', 'network-attach'))
        self.assertFalse(d['verified'])

    def test_att_keeps_factory_profile_without_its_file(self):
        self.assertEqual(self.decide(identity('310', '410'))['mcfg'], 'factory')
        self.assertIn('not installed', self.decide(identity('310', '410'))['note'])
        self.assertEqual(self.decide(identity('310', '410'), att=True)['mcfg'], 'att-qrel1695')

    def test_verizon_is_honest(self):
        d = self.decide(identity('311', '480'))
        self.assertEqual((d['mcfg'], d['apn'], d['volte']), ('factory', 'vzwinternet', 'no'))
        self.assertIn('data at best', d['note'])

    def test_eu_falls_back_to_unambiguous_mbpi_apn(self):
        d = self.decide(identity('262', '01'))
        self.assertEqual((d['mcfg'], d['apn'], d['apn_source'], d['verified']), ('factory', 'internet.telekom', 'mobile-broadband-provider-info', False))
        d = self.decide(identity('204', '08'))
        self.assertEqual((d['apn'], d['apn_source']), ('', 'network-attach'))

    def test_no_sim(self):
        self.assertEqual(self.decide(None)['state'], 'no-sim')

    def test_table_rejects_bad_rows(self):
        with tempfile.TemporaryDirectory() as t:
            bad = pathlib.Path(t) / 'bad.toml'
            for body in ('[[carrier]]\nname="x"\nnetworks=["1"]\nmcfg="vzw"\n',
                         '[[carrier]]\nname="x"\nnetworks=["1"]\napn="a;reboot"\n',
                         '[[carrier]]\nname="x"\nnetworks=[]\n'):
                bad.write_text(body)
                with self.assertRaises(ValueError):
                    m.load_table(bad)


if __name__ == '__main__':
    unittest.main()
