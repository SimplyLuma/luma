# WebKitGTK native touch coordinates and sequence lifetime

The retained patch is the exact upstream stable-branch cherry-pick
381ce36a6115f78ff8a66c3c5dc8bbef98d18efa, corresponding to main
705dc8136413d01fa61c1819e9eb4d159b711d61 and WebKit bug 324698. It subtracts
GTK's native surface transform before converting raw surface touch positions
into WebView coordinates, and cancels outstanding sequences on unmap/dialog
presentation. Mouse handling and the sandbox are unchanged.

Source: https://github.com/WebKit/WebKit/commit/381ce36a6115f78ff8a66c3c5dc8bbef98d18efa.patch
Exact SHA256: 1d201fd631b976b4a7cdd8bf450c76c2cd6ab2bfd8cb549cb6a901c8871883c9

The original installed Fedora 44 WebKitGTK 2.54.0-2 native ordinary-user Wayland
Leaf probe is RED: the compositor finger lands on the visible CHILDHOOD word,
while DOM touch coordinates still include the 145 × 95 CSD surface offset and
select a lower paragraph word. The same source candidate passes exact-word
selection in real fullscreen, whose native CSD margins are zero. This isolates
the defect from Leaf's application gesture logic.

Read-only source inspection confirms that the upstream 2.54.1 tag still lacks
this correction. Fedora 44 updates-testing currently provides 2.54.1-1; its
official spec has only the unrelated skia-s390x patch. Updating to that package
alone therefore does not resolve this defect.

The exact retained patch passes a strict zero-fuzz dry run against the official
2.54.1 release-tag WebKitWebViewBase.cpp, without offsets. Its original file
SHA256 is 5317511b7d5ec9c3f40341ed568660f65c00e4748a8c6c1b67188282fc181418.
This source check does not establish a qualified native package. Required next
steps are an owned unique WebKitGTK RPM/SRPM build from exact distro inputs,
unchanged dependency/sandbox enforcement, and installed ordinary-user native
RED/GREEN touchscreen probes at 360/500/1024/wide widths with visible shadows.
Unmap/tab/dialog-mid-contact cancellation and normal mouse/keyboard behavior
also require actual native gates. No application coordinate compensation or
runtime binary modification is accepted as the owner fix.

The full owner build caught a missing constructor prerequisite in2.54.1.
`0000-native-touch-constructor-prerequisite.patch` contains exactly the two
Shared constructor hunks of stable upstream7ea14cf7c3f99344cc6182f69a0b1515cbf28159
(317695.386), author Claudio Saavedra; SHA256 `ceccf58afb2610d1fff45ea4d86bb23ed80639a906c5421b96050a4e9505be5c`.
Upstream test runner and expectation changes are excluded. Apply before0001.
Failed full build evidence is retained; release3 requires full normal binary
and source RPM stages and the same native installed touch gates.
