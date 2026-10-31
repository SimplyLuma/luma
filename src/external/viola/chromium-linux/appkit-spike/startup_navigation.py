# SPDX-License-Identifier: GPL-3.0-only
"""Choose the primary startup destination before Chromium handles external URIs."""
def primary_startup_url(*, start_url, had_profile, urls, interactive, origin, measure_presentation):
    if start_url:
        return start_url
    # Let singleton routing handle explicit URIs from the initial blank window.
    # Loading Google first creates an unrelated occupied tab before dispatch.
    if had_profile or urls:
        return None
    if interactive:
        return 'https://www.google.com/'
    return origin + ('/cadence' if measure_presentation else '/native-window')
