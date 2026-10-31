# Native Android image packages

`luma-android-images` installs a matching system/vendor pair under
`/usr/share/waydroid-extra/images`. Each package targets x86_64 or AArch64;
its producer architecture must match the RPM architecture. The runtime package
provides host installation, application windows and desktop integration.

The package retains the actual compiler outcome, image and archive hashes,
source input identity, read-only image membership evidence, aggregate system
and vendor NOTICE files and the Figtree license. Its checks bind installed image
bytes to those records and verify that the Java/native window interface and
VINTF versions correspond. They do not establish device behavior or app compatibility.

The accompanying materials archive contains Luma's pinned Android patches,
build references and observed build receipts. It is private candidate provenance;
it is not a complete corresponding-source offer for every upstream component.
Public distribution requires the maintained licensing/source-offer and release
acceptance gates to be satisfied. Successful packaging does not close them.

Google Play Services and other proprietary Google applications are not included.
Applications depending on them require separate compatibility qualification.
Native CPU variants are preferred; an image architecture does not prove support
for every APK ABI or optional cross-architecture translation implementation.
