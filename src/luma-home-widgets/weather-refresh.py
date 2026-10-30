#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Refresh Home's saved city through Weather's existing provider and cache."""
from pathlib import Path
import subprocess
import sys


def refresh_saved_city(backend=None):
    if backend is None:
        from prairie_apps import weather_backend as backend
    places = backend.PlaceStore().list()
    if not places:
        return False
    place = places[0]
    if not backend.needs_refresh(backend.cached_forecast(place)):
        return False
    backend.fetch_forecast(place)
    return True


def main():
    try:
        if '--provider' not in sys.argv:
            # Match the installed launcher's storage/provider owner, as the
            # native widget does. No second weather API or cache is created.
            from gi.repository import Gio
            launcher = Gio.DesktopAppInfo.new('org.projectluma.Weather.desktop')
            if launcher and launcher.get_string('X-Flatpak') == 'org.projectluma.Weather':
                result = subprocess.run(['flatpak', 'run', '--command=python3',
                    'org.projectluma.Weather', '-', '--provider'],
                    input=Path(__file__).read_text(), text=True, timeout=40)
                return result.returncode
        refresh_saved_city()
        return 0
    except Exception as error:
        print(f'Weather refresh unavailable: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
