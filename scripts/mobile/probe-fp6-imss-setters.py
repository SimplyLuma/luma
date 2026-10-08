#!/usr/bin/env python3
"""Probe FP6 IMSS setters using only already-current semantic values."""

from __future__ import annotations

import asyncio

import gi
from gi.events import GLibEventLoopPolicy

gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")

from gi.repository import GLib, Qmi, Qrtr  # noqa: E402


asyncio.set_event_loop_policy(GLibEventLoopPolicy())


def current(getter, default=True) -> bool:
    try:
        value = getter()
    except GLib.GError:
        return default
    return value if isinstance(value, bool) else default


async def send(client, name, configure) -> None:
    request = Qmi.MessageImsSetImsServicesEnabledSettingInput()
    configure(request)
    try:
        response = await client.set_ims_services_enabled_setting(request, 5)
        response.get_result()
    except GLib.GError as error:
        print(
            f"event=imss-setter field={name} status=rejected code={error.code}"
            " identifiers=0"
        )
    else:
        print(f"event=imss-setter field={name} status=accepted identifiers=0")


async def probe() -> int:
    bus = await Qrtr.Bus.new(3000)
    node = bus.peek_node(0)
    if not node:
        raise RuntimeError("modem node absent")
    device = await Qmi.Device.new_from_node(node)
    await device.open(Qmi.DeviceOpenFlags.AUTO, 3)
    client = await device.allocate_client(Qmi.Service.IMS, Qmi.CID_NONE, 3)
    try:
        binding = Qmi.MessageImsBindInput()
        binding.set_binding(0)
        await client.bind(binding, 3)
        settings = await client.get_ims_services_enabled_setting(None, 3)
        fields = (
            (
                "voice",
                lambda request: request.set_ims_voice_over_lte_enable(
                    current(settings.get_ims_voice_service_enabled)
                ),
            ),
            (
                "sms",
                lambda request: request.set_ims_sms_service_enable(
                    current(settings.get_ims_sms_service_enabled)
                ),
            ),
            (
                "ut",
                lambda request: request.set_ims_ut_service_enable(
                    current(settings.get_ims_ut_service_enabled)
                ),
            ),
            (
                "wifi-voice",
                lambda request: request.set_ims_voice_wifi_service_enable(
                    current(settings.get_ims_voice_wifi_service_enabled)
                ),
            ),
            (
                "cellular-preference",
                lambda request: request.set_ims_call_mode_preference(
                    Qmi.ImsCallModePreference.CELLULAR
                ),
            ),
        )
        for name, configure in fields:
            await send(client, name, configure)
        print("event=imss-setter-probe status=complete identifiers=0")
        return 0
    finally:
        await device.release_client(
            client, Qmi.DeviceReleaseClientFlags.RELEASE_CID, timeout=3
        )


if __name__ == "__main__":
    raise SystemExit(asyncio.get_event_loop().run_until_complete(probe()))
