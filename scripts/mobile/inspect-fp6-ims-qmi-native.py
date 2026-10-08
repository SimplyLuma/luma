#!/usr/bin/env python3
"""Read-only Qualcomm IMS state probe for the Fairphone 6.

The probe binds temporary QMI clients to subscription 0 and reports only
coarse state. It never prints subscriber identity, addresses, APNs, SIP
identifiers, or raw QMI payloads, and it issues no persistent/set operation.
"""

from __future__ import annotations

import asyncio
import sys

import gi
from gi.events import GLibEventLoopPolicy

gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")

from gi.repository import GLib, Qmi, Qrtr  # noqa: E402


asyncio.set_event_loop_policy(GLibEventLoopPolicy())


def optional(getter, default="unreported"):
    try:
        value = getter()
    except GLib.GError as error:
        if error.domain.startswith("qmi_"):
            return default
        raise
    if hasattr(value, "value_nick"):
        return value.value_nick
    if hasattr(value, "name"):
        return value.name.lower()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


async def release(device, client) -> None:
    if client is not None:
        await device.release_client(
            client, Qmi.DeviceReleaseClientFlags.RELEASE_CID, timeout=3
        )


async def inspect() -> int:
    bus = await Qrtr.Bus.new(3000)
    node = bus.peek_node(0)
    if not node:
        print("event=ims-qmi-inspection status=no-modem-node")
        return 1

    device = await Qmi.Device.new_from_node(node)
    await device.open(
        Qmi.DeviceOpenFlags.AUTO | Qmi.DeviceOpenFlags.EXPECT_INDICATIONS, 3
    )

    imsa = ims = nas = None
    try:
        imsa = await device.allocate_client(Qmi.Service.IMSA, Qmi.CID_NONE, 3)
        imsa_bind = Qmi.MessageImsaBindInput()
        imsa_bind.set_binding(0)
        await imsa.bind(imsa_bind, 3)

        registration = await imsa.get_ims_registration_status(None, 3)
        services = await imsa.get_ims_services_status(None, 3)
        print(
            "event=imsa-state"
            f" registered={optional(registration.get_ims_registration_status)}"
            f" rat={optional(registration.get_ims_registration_technology)}"
            f" voice={optional(services.get_ims_voice_service_status)}"
            f" sms={optional(services.get_ims_sms_service_status)}"
            f" ut={optional(services.get_ims_ue_to_tas_service_status)}"
        )

        ims = await device.allocate_client(Qmi.Service.IMS, Qmi.CID_NONE, 3)
        ims_bind = Qmi.MessageImsBindInput()
        ims_bind.set_binding(0)
        await ims.bind(ims_bind, 3)
        enabled = await ims.get_ims_services_enabled_setting(None, 3)
        print(
            "event=imss-state"
            f" registration={optional(enabled.get_ims_registration_service_enabled)}"
            f" voice={optional(enabled.get_ims_voice_service_enabled)}"
            f" sms={optional(enabled.get_ims_sms_service_enabled)}"
            " mobile_data=unsupported-by-installed-libqmi"
            " call_mode=unsupported-by-installed-libqmi"
        )

        nas = await device.allocate_client(Qmi.Service.NAS, Qmi.CID_NONE, 3)
        selection = await nas.get_system_selection_preference(None, 3)
        print(
            "event=nas-voice-state"
            f" preference={optional(selection.get_voice_domain_preference)}"
            f" usage={optional(selection.get_usage_preference)}"
        )
        print("event=ims-qmi-inspection status=complete writes=0 identifiers=0")
        return 0
    finally:
        await release(device, nas)
        await release(device, ims)
        await release(device, imsa)


def main() -> int:
    try:
        return asyncio.get_event_loop().run_until_complete(inspect())
    except Exception as error:  # Keep diagnostics structural and identifier-free.
        print(
            "event=ims-qmi-inspection status=error"
            f" type={type(error).__name__}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
