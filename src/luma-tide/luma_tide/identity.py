# SPDX-License-Identifier: Apache-2.0
"""Tide's production and contained development-preview identities."""
APP_ID = 'org.projectluma.Tide'
PREVIEW_APP_ID = APP_ID + '.LumaUIPreview'


def validate_application_id(application_id: str) -> str:
    if application_id not in (APP_ID, PREVIEW_APP_ID):
        raise ValueError('Tide must use its production or LumaUI preview identity')
    return application_id


def mpris_name(application_id: str) -> str:
    validate_application_id(application_id)
    suffix = '.LumaUIPreview' if application_id == PREVIEW_APP_ID else ''
    return 'org.mpris.MediaPlayer2.Tide' + suffix
