# SPDX-License-Identifier: Apache-2.0
"""Portable NoCloud seed writer for Windows hosts."""


def create(directory):
    import pycdlib
    image = pycdlib.PyCdlib()
    image.new(interchange_level=3, joliet=3, rock_ridge='1.09', vol_ident='cidata')
    try:
        for source, iso_name in (('user-data', 'USER_DAT'), ('meta-data', 'META_DAT')):
            image.add_file(str(directory/source), iso_path='/'+iso_name+'.;1',
                rr_name=source, joliet_path='/'+source)
        image.write(str(directory/'seed.iso.new'))
    finally:
        image.close()
