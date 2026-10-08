# FP6 libqmi GNSS input

This directory preserves the two upstream commits ending at
`30f3e998e6cbda364ac1bc73223de20561e6d555`, immediately after libqmi merge
request !470. The substantive change adds QMI LOC Register Events client-name,
client-type, and positioning-request-notification TLVs. The second commit marks
the new unstable API as version 1.39.1.

Fedora 44 ships libqmi 1.36.0, so the API is not present in the stock Luma
phone userspace. `packaging/rpm/libqmi-fp6.spec` builds the exact upstream
snapshot; these mail patches retain authorship and make the delta reviewable.
The first two mail patches are already present in the pinned snapshot and are
not applied a second time. Patch 0003 is Luma's narrow qmicli fix for explicitly
typed PDC uploads; upstream qmicli otherwise hard-codes every load as software
and frees borrowed mapped-file storage before submitting its first chunk.

Source: <https://gitlab.freedesktop.org/mobile-broadband/libqmi/-/merge_requests/470>

Building an unsigned RPM is not permission to install it or enable location on
a phone.
