# FP6 ModemManager GNSS input

These two patches are the Fairphone 6 GNSS half carried by Catcrafts on top of
ModemManager commit `d776ea38d29ca472a12323c1d45002ee19a66f57`:

1. register QMI LOC events before starting the engine and identify the client
   as application-framework (AFW), with a retry for engines that reject the
   added identification TLVs; and
2. consume successful position-report indications and synthesize minimal
   GGA/RMC sentences only when the engine emits no native NMEA.

The series is still a draft upstream change and has only been physically
validated on an FP6. Reordering LOC setup affects all QMI modems, so other
modems require regression testing before this can become a general Fedora
carry.

Source: <https://gitlab.freedesktop.org/mobile-broadband/ModemManager/-/merge_requests/1463>

The third patch is a Luma-authored correctness fix for the pinned snapshot's
netlink transaction completion. The hash table owns and frees a transaction on
removal, so the callback must be copied before that removal; the same patch
also reads `NLMSG_ERROR` from the message currently being iterated. Repeated
FP6 qmap creation produced a matching use-after-free crash in
`netlink_messages_cb` before this carry.

The package build, package installation, location enablement, and outdoor fix
test are four separate gates.

The fourth patch makes the generic AT capability probe add 5GNR when the modem
accepts `+C5GREG=?` although `+WS46=?` lists no NG-RAN mode. The Titan 2 Elite's
MediaTek modem registers on 5G SA that way; without the capability ModemManager
never reads the 5GS registration state and reports the modem unregistered. QMI
modems (FP6) do not use this path.

The fifth patch adds an opt-in udev tag, `ID_MM_AT_VOICE_IDLE_CALL_LIST_POLL`
(seconds), that reloads the call list while idle. MediaTek IMS call control
reports incoming calls only to its MIPC host channel, so the AT port sees no
RING, +CRING or +CLIP although +CLCC lists the call. Ports without the tag are
unaffected.

The sixth patch adds bearers that set up packet data through an external
system service, named by the `ID_MM_AT_DATA_SERVICE` port tag. MediaTek CCCI
modems bind a PDN to a net port only through their MIPC host channel, which
`luma-modemd` owns on the Titan 2 Elite. Name servers the service reports
(from the network's PCO) go into the bearer's IP configuration. The seventh patch accepts MediaTek's
vendor voice mode 20 (calls over IMS) in `+CLCC`; such calls were skipped as
non-voice calls.

The eighth patch gives the generic modem a low power state through `+CFUN`
when the primary port is tagged `ID_MM_AT_CFUN_POWER_DOWN=<fun>`. Without it
airplane mode only disabled the modem and left its radio on.

The ninth patch ignores the `<AcT>` of registration replies for a domain that
is not registered. The Titan 2 Elite answers `+CREG: 2,0,<lac>,<ci>,0` while
registered over 5GS, and that GSM value replaced 5GNR. The tenth patch enables
5GS registration reports (`+C5GREG=2`); without them `+C5GREG?` carries no
access technology, so a modem on 5G SA showed none.

The eleventh patch loads and sets the initial EPS bearer settings through the
external data service (`GetInitialEpsBearerSettings`,
`SetInitialEpsBearerSettings`). MediaTek CCCI modems take their initial attach
APN over MIPC, not as a `+CGDCONT` context, so the generic path failed after
powering the radio down.
