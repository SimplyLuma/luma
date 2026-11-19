# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib,sys
root=pathlib.Path(sys.argv[1]);active=pathlib.Path(sys.argv[2]).read_text();query=pathlib.Path(sys.argv[3]).read_text()
s=(root/"bootctrl_impl.c").read_text();a=s.index("unsigned get_active_boot_slot()");b=s.index("\n/*",a);assert s[a:b] in active
s=(root/"qbootctl.c").read_text();a=s.index("\tcase 'b':",s.index("switch (optflag)"));b=s.index("\tcase 'x':",a);assert s[a:b] in query
