#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate callers against the native packaged Shell extension interface."""
import pathlib,re,sys
if len(sys.argv)!=4:raise SystemExit('extension.js prepared-cc-luma-live-windows.c packaged-shellDBus.js required')
extension,settings,native=(pathlib.Path(p).read_text() for p in sys.argv[1:])
manager=native.split('class GnomeShellExtensions {',1)[1].split('\nclass ',1)[0]
export=re.search(r"this\._dbusImpl\.export\(Gio\.DBus\.session, ['\"]([^'\"]+)['\"]\)",manager)
assert export,'Native Shell extension manager export missing'
expected=('org.gnome.Shell',export.group(1),'org.gnome.Shell.Extensions')
actual=tuple(re.search(r"const "+name+r" = ['\"]([^'\"]+)['\"]",extension).group(1) for name in ('EXTENSIONS_BUS_NAME','EXTENSIONS_OBJECT_PATH','EXTENSIONS_INTERFACE'))
assert actual==expected,(actual,expected)
call=re.search(r'g_dbus_connection_call \(bus, "([^"\n]+)", "([^"\n]+)", "([^"\n]+)",\s*json_node_get_boolean \(value\) \? "EnableExtension" : "DisableExtension"',settings)
assert call,'Actual Settings tiling activation call missing'
assert call.groups()==expected,(call.groups(),expected)
assert 'EnableExtension' in manager and 'DisableExtension' in manager,'Native methods missing'
print('PASS Quick Options and Settings endpoints match actual native Shell manager')
