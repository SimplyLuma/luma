# SPDX-License-Identifier: Apache-2.0
"""Register a portable Linux build with the user's application menu and dock."""
import os
from pathlib import Path
import tempfile

from .core import TesterError


def install(executable=None, data_home=None):
    executable = executable or os.environ.get('APPIMAGE') or os.environ.get('LUMA_TESTER_EXECUTABLE')
    if not executable:
        raise TesterError('Open the portable build to add it to Applications.')
    executable = Path(executable).resolve()
    if any(character in str(executable) for character in ('\n', '\r')):
        raise TesterError('The portable executable path contains a line break.')
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise TesterError('The portable executable is not available.')
    root = Path(data_home or os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')

    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.chmod(0o644)
        temporary.replace(path)

    # Exec values use Desktop Entry quoting, not shell quoting. In particular,
    # a literal percent must survive field-code expansion and paths may contain
    # spaces, quotes, backticks, dollar signs and backslashes.
    quoted = str(executable).replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        quoted = quoted.replace(character, '\\' + character)
    quoted = quoted.replace('\\', '\\\\')
    arguments = ' --appimage-extract-and-run' if executable.suffix == '.AppImage' else ''
    entry = ('[Desktop Entry]\nType=Application\nName=Luma Tester\n'
             'Comment=Try Luma or develop apps for Mobile and Desktop\n'
             f'Exec=/usr/bin/env -- "{quoted}"{arguments} gui\nIcon=io.luma.Tester\n'
             'Terminal=false\nCategories=Development;Emulator;\n'
             'StartupNotify=true\nStartupWMClass=io.luma.Tester\n')
    icon = root / 'icons/hicolor/scalable/apps/io.luma.Tester.svg'
    write(icon, Path(__file__).with_name('io.luma.Tester.svg').read_bytes())
    desktop = root / 'applications/io.luma.Tester.desktop'
    write(desktop, entry.encode())
    return desktop
