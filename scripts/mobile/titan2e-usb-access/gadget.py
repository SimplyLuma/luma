#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Opt-in Titan administrative USB ECM transport; no shell or mass storage.

Uses the Linux configfs gadget ABI, documented at
https://docs.kernel.org/usb/gadget_configfs.html . No kernel patches are applied.
"""
import argparse
import os
from pathlib import Path
import subprocess
import time

NAME = "luma-titan-admin"
SERIAL = "luma-titan2e-project-titan"
DEVICE_MAC = "02:54:49:54:41:01"
HOST_MAC = "02:54:49:54:41:02"
COMPATIBLE = b"unihertz,titan2-elite"
CONFIGFS = Path("/sys/kernel/config")
UDCS = Path("/sys/class/udc")
BOARD = Path("/sys/firmware/devicetree/base/compatible")


def check_board(path=BOARD):
    if COMPATIBLE not in path.read_bytes().split(b"\0"):
        raise RuntimeError("refusing non-Titan hardware")


def select_controller(root=UDCS):
    controllers = sorted(root.iterdir())
    if len(controllers) != 1:
        raise RuntimeError("expected exactly one USB device controller")
    return controllers[0].name


def check_available(parent, controller):
    for gadget in parent.iterdir():
        udc = gadget / "UDC"
        if udc.exists() and udc.read_text().strip() == controller:
            raise RuntimeError("USB controller already owned; refusing to replace its gadget")
    if (parent / NAME).exists():
        raise RuntimeError("existing administrative gadget; stop it before restarting")


def put(path, value):
    path.write_text(str(value) + "\n")


def stop(parent):
    gadget = parent / NAME
    if not gadget.exists():
        return
    if (gadget / "strings/0x409/serialnumber").read_text().strip() != SERIAL:
        raise RuntimeError("refusing to remove an unrecognized gadget")
    remove_created(gadget)


def remove_created(gadget):
    if (gadget / "UDC").read_text().strip():
        put(gadget / "UDC", "")
    (gadget / "configs/c.1/ecm.admin").unlink(missing_ok=True)
    for relative in ("configs/c.1/strings/0x409", "configs/c.1", "functions/ecm.admin", "strings/0x409"):
        path = gadget / relative
        if path.exists():
            path.rmdir()
    gadget.rmdir()


def start():
    check_board()
    if not os.path.ismount(CONFIGFS):
        subprocess.run(["mount", "-t", "configfs", "configfs", str(CONFIGFS)], check=True)
    parent = CONFIGFS / "usb_gadget"
    deadline = time.monotonic() + 20
    while not list(UDCS.iterdir()) and time.monotonic() < deadline:
        time.sleep(0.25)
    controller = select_controller()
    check_available(parent, controller)
    gadget = parent / NAME
    gadget.mkdir()
    try:
        # Descriptors are populated before binding. Other gadgets are never stopped.
        for name, value in (("idVendor", "0x1d6b"), ("idProduct", "0x0104"),
                            ("bcdUSB", "0x0200"), ("bcdDevice", "0x0100")):
            put(gadget / name, value)
        strings = gadget / "strings/0x409"
        strings.mkdir()
        for name, value in (("manufacturer", "Luma"), ("product", "Titan USB administration"),
                            ("serialnumber", SERIAL)):
            put(strings / name, value)
        config = gadget / "configs/c.1"
        config.mkdir()
        (config / "strings/0x409").mkdir()
        put(config / "strings/0x409/configuration", "Key-authenticated administration")
        function = gadget / "functions/ecm.admin"
        function.mkdir()
        put(function / "ifname", "titanusb0")
        put(function / "dev_addr", DEVICE_MAC)
        put(function / "host_addr", HOST_MAC)
        (config / "ecm.admin").symlink_to(function)
        put(gadget / "UDC", controller)
    except Exception:
        remove_created(gadget)
        raise
    print("Titan USB ECM ready", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("root required")
    if args.stop:
        check_board()
        stop(CONFIGFS / "usb_gadget")
    else:
        start()


if __name__ == "__main__":
    main()
