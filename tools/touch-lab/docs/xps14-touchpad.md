# XPS 14 (DA14260) touchpad

`VEN_2C2F:00 2C2F:0034`, I2C HID, a Sensel haptic pressure pad. hid-multitouch
binds it; libinput's `50-system-dell.quirks` adds `INPUT_PROP_PRESSUREPAD`.
The report descriptor is `touchlab/data/xps14-2c2f-0034.desc.hex` (from
`/sys/bus/hid/devices/0018:2C2F:0034.*/report_descriptor`).

## Input reports

| ID | Size | Contents |
|---|---|---|
| 2 | 4 | Mouse (5 buttons, relative X/Y), used before the host selects touchpad mode |
| 3 | 40 | Touchpad: 5 contacts × {Confidence 1 bit, Tip Switch 1 bit, Contact ID 6 bits, X 16 bits (0–4723, 149.6 mm), Y 16 bits (0–2298, 73.8 mm), Tip Pressure 16 bits (0–32767)}, Scan Time 16 bits (100 µs), Contact Count 8 bits, Button 1 |
| 9 | 21 | Vendor (page 0xFF00, usage 1): 20 opaque bytes |

There is no Width, Height or Azimuth: contact size and shape aren't reported,
so force, position and motion carry the signal.

## Feature reports

| ID | Contents |
|---|---|
| 3 | Vendor (0xFF00:1), 1 byte, in the touchpad collection |
| 4 | Input Mode (Device Configuration) |
| 6 | Surface Switch, Button Switch |
| 7 | Vendor 0xC5, 256 bytes: the Precision Touchpad certification blob |
| 8 | Contact Count Maximum (4 bits), Pad Type (4 bits) |
| 10 | Latency Mode, 1 bit |
| 11 | Haptics page: Simple Haptic Controller / Intensity, 0–100 |
| 12 | Vendor (0xFF00:1), 1 byte |

The recorder reads every feature report except 7 once per session with
GET_FEATURE and stores the values in `session.json`. It never sends SET_FEATURE
or output reports.

## Vendor report 9

A separate application collection on the vendor page with one 20-byte Output
report and one 20-byte Input report under the same ID: a request/response
channel. Nothing in the kernel binds it (hid-multitouch handles only the
digitizer collections), fwupd doesn't claim the device, and no usage inside
it is named, so the bytes are the vendor's own protocol: most likely
diagnostics, configuration or firmware update. It carries no touch data in
the descriptor's terms. The recorder keeps every input report 9 the device
sends unprompted (in the raw `h` stream); it never writes an output report 9,
because an unknown command to this channel could change the pad's state.

## Kernel and libinput behaviour

Checked by replaying this descriptor through UHID on kernel 6.19 with
libinput 1.31.3 (`e2e/uhid_touchpad.py`); the XPS runs 7.2 and its first
recording will confirm it:

- A contact with Confidence=0 stays in evdev with its slot's
  `ABS_MT_TOOL_TYPE` set to `MT_TOOL_PALM` (2). libinput then reports
  `palm detected (tool-palm)`.
- `MSC_TIMESTAMP` carries the scan time in µs.
- Positions in evdev equal the HID values (no scaling), which is how
  contacts are matched across the two streams.
- libinput enables area-based thumb detection and no pressure-based palm or
  thumb detection on this pad.
