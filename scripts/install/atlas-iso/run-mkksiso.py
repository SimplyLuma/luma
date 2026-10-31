#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Retain Lorax's EFI partition update and refresh its ISO-file boot route."""
from pathlib import Path
import shutil
import sys


def main():
    from pylorax.cmdline import mkksiso
    original = mkksiso.RebuildEFIBoot

    def rebuild(input_iso, temporary):
        image = original(input_iso, temporary)
        # Lorax44 updates appended partition2 but leaves an existing
        # /images/efiboot.img unchanged. ARM El Torito still boots that file.
        # Include the same verified FAT bytes in its normal update list.
        if 'images/efiboot.img' in mkksiso.GetISODetails(input_iso)[1]:
            target = Path(temporary) / 'images/efiboot.img'
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(image.name, target)
        return image

    mkksiso.RebuildEFIBoot = rebuild
    return mkksiso.main()


if __name__ == '__main__':
    sys.exit(main())
