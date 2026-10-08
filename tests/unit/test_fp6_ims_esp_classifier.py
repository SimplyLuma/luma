from __future__ import annotations

import importlib.util
import ipaddress
import socket
import struct
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts/mobile/classify-fp6-ims-esp.py"
SPEC = importlib.util.spec_from_file_location("fp6_ims_esp_classifier", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def ipv6_esp(destination: str, spi: int) -> bytes:
    source = ipaddress.ip_address("2001:db8::1").packed
    target = ipaddress.ip_address(destination).packed
    header = bytes.fromhex("6000000000043240") + source + target
    return header + struct.pack("!I", spi)


class EspClassifierTest(unittest.TestCase):
    def test_known_inbound_server_path(self) -> None:
        local = {ipaddress.ip_address("2001:db8::2")}
        states = {0x11223344: (True, 2)}
        self.assertEqual(
            MODULE.classify(ipv6_esp("2001:db8::2", 0x11223344), 0, local, states),
            "in-server",
        )

    def test_unknown_inbound_spi_is_visible_without_value(self) -> None:
        local = {ipaddress.ip_address("2001:db8::2")}
        self.assertEqual(
            MODULE.classify(ipv6_esp("2001:db8::2", 0xAABBCCDD), 0, local, {}),
            "in-unknown",
        )

    def test_outbound_packet_uses_packet_type(self) -> None:
        local = {ipaddress.ip_address("2001:db8::2")}
        states = {0x55667788: (False, 1)}
        packet = ipv6_esp("2001:db8::9", 0x55667788)
        self.assertEqual(
            MODULE.classify(packet, MODULE.PACKET_OUTGOING, local, states),
            "out-client",
        )

    def test_non_esp_packet_is_ignored(self) -> None:
        packet = bytearray(ipv6_esp("2001:db8::2", 1))
        packet[6] = socket.IPPROTO_TCP
        self.assertIsNone(MODULE.esp_header(bytes(packet)))

    def test_plain_ip_xfrm_output(self) -> None:
        local = {ipaddress.ip_address("2001:db8::2")}
        output = """src 2001:db8::1 dst 2001:db8::2
\tproto esp spi 0x11223344 reqid 2 mode transport
src 2001:db8::2 dst 2001:db8::1
\tproto esp spi 0x55667788 reqid 1 mode transport
"""
        self.assertEqual(
            MODULE.parse_xfrm_text(output, local),
            {0x11223344: (True, 2), 0x55667788: (False, 1)},
        )


if __name__ == "__main__":
    unittest.main()
