"""Actual libsecret round trip in an explicitly disposable user session bus."""
import os,secrets,subprocess,time
from pathlib import Path
import gi
gi.require_version('Gio','2.0')
from gi.repository import Gio,GLib
from luma_continuity.account import AccountConfig,SecretTokens,AccountError
assert os.environ.get('LUMA_CONNECT_PRIVATE_SMOKE')=='1'
private=Path(os.environ['XDG_DATA_HOME']).parent
assert private.name.startswith('secret-smoke-') and private.is_dir()
control=private/'control';control.mkdir(mode=0o700)
password=secrets.token_hex(32).encode()
process=subprocess.Popen(['gnome-keyring-daemon','--foreground','--unlock','--components=secrets',
    '--control-directory',str(control)],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
process.stdin.write(password);process.stdin.close()
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
try:
    deadline=time.monotonic()+5
    while not bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',
        'NameHasOwner',GLib.Variant('(s)',('org.freedesktop.secrets',)),None,Gio.DBusCallFlags.NONE,1000,None).unpack()[0]:
        if time.monotonic()>deadline:raise RuntimeError('Private Secret Service did not start')
        time.sleep(.05)
    tokens=SecretTokens(AccountConfig('https://synthetic.example.test/realm','https://api.example.test','fixture-native'))
    assert tokens.get() is None
    tokens.set({'access_token':'synthetic-fixture-only','refresh_token':'synthetic-refresh-only'})
    assert tokens.get()['access_token']=='synthetic-fixture-only'
    tokens.clear();assert tokens.get() is None
    tokens.set({'access_token':'synthetic-fixture-only'})
    collection=tokens._collection()
    bus.call_sync('org.freedesktop.secrets','/org/freedesktop/secrets','org.freedesktop.Secret.Service',
        'Lock',GLib.Variant('(ao)',([collection.get_object_path()],)),None,0,1000,None)
    try:tokens.get()
    except AccountError as error:assert error.code=='keyring_locked'
    else:raise AssertionError('Locked store looked signed out')
    print('PASS actual libsecret store/read/clear and locked-state detection on private bus; synthetic tokens only; unlock recovery not exercised here')
finally:
    process.terminate();process.wait(timeout=5)
