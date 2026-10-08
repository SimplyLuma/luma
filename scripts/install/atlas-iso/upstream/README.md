# Anaconda native installer clock references

`anaconda-timezone-44.30.py` is the unmodified native `pyanaconda/timezone.py`
from Fedora 44's Anaconda 44.30, extracted from the actual ISO 20261005.6
(SHA256 `b22db9ec146e7a5618455e73b0964fe6f044fb840bed70755ae76ff72a1d0909`).
Its SHA256 is
`680c0688d7185db06bc97dd31913e8bcb7acce2bb113e8e3d40d64ca86c5f030`.

`anaconda-ostree-installation-44.30.py` is the same runtime's unmodified
`pyanaconda/modules/payloads/payload/rpm_ostree/installation.py`.
Its SHA256 is
`614ac3f3061d959144e0a2e80919012c7254d44b1fab04daea35b2bef52f3f86`.

Upstream: https://github.com/rhinstaller/anaconda. Copyright Red Hat;
GPL-2.0-or-later. The complete original copyright/license header is retained.
This is a source reference for the maintained installer-only downstream patch
and native-function regression tests; it is not a second installed module.
`prepare-runtime-clock.py` refuses any different native source and the ISO
builder verifies that both complete patched modules reached final stage 2.
