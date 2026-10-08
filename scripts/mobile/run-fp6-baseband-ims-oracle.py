#!/usr/bin/env python3
"""Bounded native Qualcomm IMS registration/incoming-call oracle for FP6.

This is a single-owner diagnostic, not a production daemon.  It stops Luma's
userspace SIP client, enables only the modem IMS registration bit, observes
coarse IMSA/QMI Voice state, restores the prior semantic state, and forces a
fresh Luma registration.  Subscriber identities and QMI payloads are never
printed or persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import time
from pathlib import Path

import gi
from gi.events import GLibEventLoopPolicy

gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")

from gi.repository import GLib, Qmi, Qrtr  # noqa: E402


asyncio.set_event_loop_policy(GLibEventLoopPolicy())

EXPECTED_KERNEL = "7.1.2-luma-fp-ims1"
SERVICE = "luma-fp6-imsd.service"
DROPIN = Path("/run/systemd/system/luma-fp6-imsd.service.d/90-baseband-oracle.conf")


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def optional(getter):
    try:
        return getter()
    except GLib.GError:
        return None


def enum_name(value) -> str:
    if value is None:
        return "unreported"
    if hasattr(value, "value_nick"):
        return str(value.value_nick)
    if hasattr(value, "name"):
        return str(value.name).lower()
    return str(value).lower()


async def release(device, client) -> None:
    if client is not None:
        await device.release_client(
            client, Qmi.DeviceReleaseClientFlags.RELEASE_CID, timeout=3
        )


async def registration_state(imsa) -> str:
    response = await imsa.get_ims_registration_status(None, 3)
    return enum_name(optional(response.get_ims_registration_status))


async def active_call_count(voice) -> int:
    try:
        response = await voice.get_all_call_info(None, 3)
        information = response.get_call_information()
    except (GLib.GError, TypeError):
        return 0
    return len(information) if hasattr(information, "__len__") else 0


def bool_value(value, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def get_first(obj, *names):
    for name in names:
        getter = getattr(obj, name, None)
        if getter is not None:
            return optional(getter)
    return None


def set_first(obj, value, *names) -> bool:
    for name in names:
        setter = getattr(obj, name, None)
        if setter is not None:
            setter(value)
            return True
    return False


async def set_services(ims, enabled: bool, full: bool, original=None) -> None:
    request = Qmi.MessageImsSetImsServicesEnabledSettingInput()
    if not set_first(
        request,
        enabled,
        "set_ims_service_enabled",
        "set_ims_registration_service_enable",
    ):
        raise RuntimeError("registration setter absent")
    if full:
        if enabled:
            set_first(
                request,
                True,
                "set_ims_voice_over_lte_enable",
                "set_ims_voice_service_enable",
            )
            request.set_ims_ut_service_enable(True)
            request.set_ims_voice_wifi_service_enable(True)
            if set_first(request, True, "set_ims_mobile_data_enable"):
                set_first(request, 3, "set_ims_service_mask_by_operator_enable")
                set_first(
                    request,
                    Qmi.ImsCallModePreference.CELLULAR,
                    "set_ims_call_mode_preference",
                )
        else:
            set_first(
                request,
                bool_value(original["voice"], True),
                "set_ims_voice_over_lte_enable",
                "set_ims_voice_service_enable",
            )
            request.set_ims_ut_service_enable(bool_value(original["ut"], True))
            request.set_ims_voice_wifi_service_enable(bool_value(original["wifi"], True))
            if original.get("mobile_data") is not None:
                set_first(
                    request,
                    bool_value(original["mobile_data"], True),
                    "set_ims_mobile_data_enable",
                )
            if original.get("operator_mask") is not None:
                set_first(
                    request,
                    int(original["operator_mask"]),
                    "set_ims_service_mask_by_operator_enable",
                )
            if original.get("call_mode") is not None:
                set_first(
                    request,
                    original["call_mode"],
                    "set_ims_call_mode_preference",
                )
    response = await ims.set_ims_services_enabled_setting(request, 5)
    response.get_result()


def verify_host(bridge: Path) -> None:
    if os.geteuid() != 0:
        raise RuntimeError("must run as root")
    if run("uname", "-r").stdout.strip() != EXPECTED_KERNEL:
        raise RuntimeError("kernel identity mismatch")
    cmdline = Path("/proc/cmdline").read_text(encoding="utf-8")
    if "androidboot.slot_suffix=_b" not in cmdline.split():
        raise RuntimeError("slot identity mismatch")
    if not bridge.is_file() or bridge.is_symlink() or not os.access(bridge, os.X_OK):
        raise RuntimeError("bridge identity mismatch")
    if run("systemctl", "is-active", SERVICE, check=False).stdout.strip() != "active":
        raise RuntimeError("userspace IMS service is not active")


def recent_fault_count(started: int) -> int:
    journal = run(
        "journalctl", "-k", "--since", f"@{started}", "--no-pager", check=False
    ).stdout.lower()
    patterns = ("oops:", "kernel panic", "hangcheck", "gmu timeout", "tee fault")
    return sum(journal.count(pattern) for pattern in patterns)


def clear_luma_xfrm() -> None:
    states = run("ip", "xfrm", "state").stdout
    policies = run("ip", "xfrm", "policy").stdout
    state_count = len(re.findall(r"^src\s+\S+\s+dst\s+\S+$", states, re.MULTILINE))
    state_reqids = {int(value) for value in re.findall(r"\breqid\s+(\d+)\b", states)}
    policy_reqids = {int(value) for value in re.findall(r"\breqid\s+(\d+)\b", policies)}
    if state_count != 4 or not state_reqids or not state_reqids.issubset({1, 2}):
        raise RuntimeError("XFRM state ownership mismatch")
    if not policy_reqids or not policy_reqids.issubset({1, 2}):
        raise RuntimeError("XFRM policy ownership mismatch")
    run("ip", "xfrm", "state", "flush")
    run("ip", "xfrm", "policy", "flush")


def force_fresh_luma(started: int) -> bool:
    started = int(time.time())
    DROPIN.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    DROPIN.write_text("[Service]\nEnvironment=RESUME=0\n", encoding="utf-8")
    os.chmod(DROPIN, 0o644)
    run("systemctl", "daemon-reload")
    run("systemctl", "restart", SERVICE)
    deadline = time.monotonic() + 90
    fresh = False
    while time.monotonic() < deadline:
        logs = run(
            "journalctl", "-u", SERVICE, "--since", f"@{started}", "--no-pager",
            check=False,
        ).stdout
        if "REGISTERED (fresh)" in logs:
            fresh = True
            break
        time.sleep(1)
    DROPIN.unlink(missing_ok=True)
    try:
        DROPIN.parent.rmdir()
    except OSError:
        pass
    run("systemctl", "daemon-reload", check=False)
    return fresh


async def oracle(args) -> int:
    started = int(time.time())
    bridge_path = Path(args.bridge)
    verify_host(bridge_path)

    bus = await Qrtr.Bus.new(3000)
    node = bus.peek_node(0)
    if not node:
        raise RuntimeError("modem node absent")
    device = await Qmi.Device.new_from_node(node)
    await device.open(
        Qmi.DeviceOpenFlags.AUTO | Qmi.DeviceOpenFlags.EXPECT_INDICATIONS, 3
    )

    ims = imsa = voice = None
    bridge = None
    registration_changed = False
    userspace_stopped = False
    incoming_seen = False
    registered = False
    restore_fresh = False
    try:
        ims = await device.allocate_client(Qmi.Service.IMS, Qmi.CID_NONE, 3)
        ims_bind = Qmi.MessageImsBindInput()
        ims_bind.set_binding(0)
        await ims.bind(ims_bind, 3)
        current = await ims.get_ims_services_enabled_setting(None, 3)
        original = {
            "registration": get_first(
                current,
                "get_ims_registration_service_enabled",
                "get_ims_registration_service_enable",
            ),
            "voice": get_first(
                current,
                "get_ims_voice_service_enabled",
                "get_ims_voice_service_enable",
            ),
            "sms": get_first(
                current,
                "get_ims_sms_service_enabled",
                "get_ims_sms_service_enable",
            ),
            "ut": get_first(
                current,
                "get_ims_ut_service_enabled",
                "get_ims_ut_service_enable",
            ),
            "wifi": get_first(
                current,
                "get_ims_voice_wifi_service_enabled",
                "get_ims_voice_wifi_service_enable",
            ),
            "mobile_data": get_first(current, "get_ims_mobile_data_enable"),
            "operator_mask": get_first(
                current, "get_ims_service_mask_by_operator"
            ),
            "call_mode": get_first(current, "get_ims_call_mode_preference"),
        }
        prior_registration = original["registration"]
        if prior_registration is True:
            raise RuntimeError("modem IMS registration was already enabled")

        imsa = await device.allocate_client(Qmi.Service.IMSA, Qmi.CID_NONE, 3)
        imsa_bind = Qmi.MessageImsaBindInput()
        imsa_bind.set_binding(0)
        await imsa.bind(imsa_bind, 3)
        if await registration_state(imsa) == "registered":
            raise RuntimeError("modem IMSA was already registered")

        voice = await device.allocate_client(Qmi.Service.VOICE, Qmi.CID_NONE, 3)

        run("systemctl", "stop", SERVICE)
        userspace_stopped = True
        clear_luma_xfrm()
        environment = os.environ.copy()
        if args.interface:
            environment["DEV"] = args.interface
        bridge = subprocess.Popen(
            [str(bridge_path), str(args.duration + 120)],
            env=environment,
            stdout=None,
            stderr=None,
        )
        await asyncio.sleep(1)
        if bridge.poll() is not None:
            raise RuntimeError("IMSDCM bridge failed to publish")

        await set_services(ims, True, args.full_openimsd, original)
        registration_changed = True
        registration_deadline = time.monotonic() + args.registration_timeout
        while time.monotonic() < registration_deadline:
            state = await registration_state(imsa)
            if state == "registered":
                registered = True
                break
            await asyncio.sleep(2)

        if not registered:
            print(
                "event=baseband-ims-oracle phase=registration status=timeout"
                f" mode={'openimsd-full' if original.get('mobile_data') is not None else 'openimsd-available-fields' if args.full_openimsd else 'registration-only'}"
                " identifiers=0"
            )
            return 3

        Path(args.ready_file).write_text(
            "event=baseband-ims-oracle phase=ready registered=true identifiers=0\n",
            encoding="utf-8",
        )
        os.chmod(args.ready_file, 0o600)
        print(
            "event=baseband-ims-oracle phase=ready registered=true"
            f" mode={'openimsd-full' if original.get('mobile_data') is not None else 'openimsd-available-fields' if args.full_openimsd else 'registration-only'}"
            " identifiers=0",
            flush=True,
        )

        call_deadline = time.monotonic() + args.duration
        while time.monotonic() < call_deadline:
            if await active_call_count(voice) > 0:
                incoming_seen = True
                print(
                    "event=baseband-ims-oracle phase=call"
                    " incoming_indication=true identifiers=0",
                    flush=True,
                )
                break
            await asyncio.sleep(0.5)
        return 0 if incoming_seen else 4
    finally:
        Path(args.ready_file).unlink(missing_ok=True)
        if registration_changed and ims is not None:
            try:
                await set_services(ims, False, args.full_openimsd, original)
            except Exception:
                pass
        if bridge is not None and bridge.poll() is None:
            bridge.terminate()
            try:
                bridge.wait(timeout=5)
            except subprocess.TimeoutExpired:
                bridge.kill()
                bridge.wait(timeout=2)
        await release(device, voice)
        await release(device, imsa)
        await release(device, ims)
        if userspace_stopped:
            restore_fresh = force_fresh_luma(started)
        faults = recent_fault_count(started)
        print(
            "event=baseband-ims-oracle phase=cleanup"
            f" registered={str(registered).lower()}"
            f" incoming_indication={str(incoming_seen).lower()}"
            f" luma_fresh_registration={str(restore_fresh).lower()}"
            f" faults={faults} identifiers=0",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge", required=True)
    parser.add_argument("--interface")
    parser.add_argument("--duration", type=int, default=150)
    parser.add_argument("--registration-timeout", type=int, default=90)
    parser.add_argument("--ready-file", default="/run/luma-baseband-ims-oracle.ready")
    parser.add_argument("--full-openimsd", action="store_true")
    args = parser.parse_args()
    if not 10 <= args.duration <= 300:
        raise SystemExit("duration must be between 10 and 300 seconds")
    if not 10 <= args.registration_timeout <= 180:
        raise SystemExit("registration timeout must be between 10 and 180 seconds")
    try:
        return asyncio.get_event_loop().run_until_complete(oracle(args))
    except Exception as error:
        print(
            "event=baseband-ims-oracle phase=failed"
            f" error={type(error).__name__} identifiers=0",
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
