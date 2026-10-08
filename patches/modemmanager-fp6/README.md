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
