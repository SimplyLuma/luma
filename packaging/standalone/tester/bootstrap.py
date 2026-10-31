# SPDX-License-Identifier: Apache-2.0
"""Initialize the relocatable Try Luma process and its private local catalog."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys

base = Path(__file__).resolve().parent
os.environ.update({
    'LUMA_APP_NAME': 'Try Luma',
    'LUMA_TESTER_EXECUTABLE': os.environ.get('APPIMAGE', str(base / 'Try Luma')),
    'GI_TYPELIB_PATH': str(base / 'usr/lib64/girepository-1.0'),
    'GSETTINGS_SCHEMA_DIR': str(base / 'usr/share/glib-2.0/schemas'),
    'GIO_EXTRA_MODULES': str(base / 'usr/lib64/gio/modules'),
    'FONTCONFIG_FILE': str(base / 'fonts.conf'),
    'XDG_DATA_DIRS': str(base / 'usr/share') + ':' + os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share'),
    'LUMA_TESTER_FIRMWARE_DIR': str(base / 'tools/firmware'),
    'LUMA_TESTER_GUEST_UPDATES': str(base / 'guest-updates'),
    'PATH': str(base / 'tools/bin') + ':' + os.environ.get('PATH', '/usr/bin:/bin'),
    # Do not combine this process's private libc/GTK with host Mesa, GLVND or
    # Vulkan drivers, even when a desktop session exports alternate paths.
    '__EGL_VENDOR_LIBRARY_FILENAMES': str(base / 'tools/egl/50_mesa.json'),
    '__GLX_VENDOR_LIBRARY_NAME': 'mesa',
    'LIBGL_DRIVERS_PATH': str(base / 'tools/dri'),
    'VK_DRIVER_FILES': ':'.join(str(p) for p in sorted((base / 'tools/vulkan').glob('*.json'))),
    'VK_LOADER_LAYERS_DISABLE': '*',
    'VK_LAYER_PATH': str(base / 'tools/vulkan-layers'),
    'VK_IMPLICIT_LAYER_PATH': str(base / 'tools/vulkan-layers'),
})
for name in ('__EGL_VENDOR_LIBRARY_DIRS', 'VK_ICD_FILENAMES', 'VK_ADD_DRIVER_FILES',
             'VK_ADD_LAYER_PATH', 'VK_ADD_IMPLICIT_LAYER_PATH', 'VK_INSTANCE_LAYERS',
             'VK_LOADER_LAYERS_ENABLE', 'VK_LOADER_LAYERS_ALLOW', 'GTK_PATH', 'GTK_MODULES'):
    os.environ.pop(name, None)
if not os.environ.get('SSL_CERT_FILE'):
    for candidate in ('/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt', '/etc/ssl/cert.pem'):
        if Path(candidate).is_file():
            os.environ['SSL_CERT_FILE'] = candidate
            break
cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'luma-tester/portable'
cache.mkdir(mode=0o700, parents=True, exist_ok=True)
key = hashlib.sha256(os.environ.get('APPIMAGE', str(base)).encode()).hexdigest()[:20]
loaders = cache / (key + '-pixbuf.cache')
loaders.write_text((base / 'pixbuf-loaders.cache').read_text().replace('@TESTER_ROOT@', str(base)))
os.environ['GDK_PIXBUF_MODULE_FILE'] = str(loaders)
if not os.environ.get('LUMA_TESTER_CATALOG'):
    catalog = cache / key
    catalog.mkdir(mode=0o700, exist_ok=True)
    for path in (base / 'catalog').glob('*.json'):
        from luma_tester.catalog import resolve_asset
        manifest = resolve_asset(json.loads(path.read_text()), base, os.environ.get('APPIMAGE'))
        temporary = catalog / (path.name + '.' + str(os.getpid()))
        temporary.write_text(json.dumps(manifest, indent=2) + '\n')
        temporary.replace(catalog / path.name)
    os.environ['LUMA_TESTER_CATALOG'] = str(catalog)
# All child Python invocations must re-enter the bundled loader/environment.
sys.executable = str(base / 'python')
args = sys.argv[1:]
if args[:1] == ['-m'] and len(args) > 1:
    sys.argv = [args[1], *args[2:]]
    runpy.run_module(args[1], run_name='__main__', alter_sys=True)
elif args:
    sys.argv = args
    runpy.run_path(args[0], run_name='__main__')
else:
    sys.argv = ['luma-tester', 'gui']
    runpy.run_module('luma_tester', run_name='__main__', alter_sys=True)
