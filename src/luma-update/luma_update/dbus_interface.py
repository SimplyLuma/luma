# SPDX-License-Identifier: Apache-2.0
"""The org.projectluma.Update1 D-Bus interface definition (one source of truth).

The build writes this XML to /usr/share/dbus-1/interfaces/org.projectluma.Update1.xml.
"""

from __future__ import annotations

from .status import DBUS_TYPES, dbus_name

BUS_NAME = "org.projectluma.Update1"
OBJECT_PATH = "/org/projectluma/Update1"
INTERFACE = "org.projectluma.Update1"
ERROR_PREFIX = "org.projectluma.Update1.Error."

ACTION_CHECK = "org.projectluma.update.check"
ACTION_DOWNLOAD = "org.projectluma.update.download"
ACTION_APPLY = "org.projectluma.update.apply"
ACTION_CHANNEL = "org.projectluma.update.set-channel"
ACTION_ROLLBACK = "org.projectluma.update.rollback"
ACTION_PREVIEW = "org.projectluma.update.enroll-preview"

#: Every method: (polkit action, or None for uid 0 only; whether the call returns when done).
#: The daemon dispatches from this table and the tests hold the documentation to it.
METHODS = {
    "Check": (ACTION_CHECK, False),
    "Download": (ACTION_DOWNLOAD, False),
    "Cancel": (ACTION_DOWNLOAD, True),
    "Apply": (ACTION_APPLY, True),
    "SetChannel": (ACTION_CHANNEL, True),
    "SetChannelNow": (ACTION_CHANNEL, True),
    "Rollback": (ACTION_ROLLBACK, True),
    "EnrollPreview": (ACTION_PREVIEW, True),
    "LeavePreview": (ACTION_PREVIEW, True),
    "AcknowledgeRollback": (ACTION_CHECK, True),
    "SetAutomaticDownload": (ACTION_DOWNLOAD, True),
    "IgnoreVersion": (ACTION_CHECK, True),
    "ClearIgnoredVersion": (ACTION_CHECK, True),
    "AdoptChannel": (ACTION_CHANNEL, True),
    "Automatic": (None, False),
}

#: logind's own actions, checked for the caller of Apply() (luma-updated is root,
#: and logind never asks polkit about root).
LOGIN1_REBOOT = "org.freedesktop.login1.reboot"
LOGIN1_REBOOT_MULTIPLE_SESSIONS = "org.freedesktop.login1.reboot-multiple-sessions"

ERRORS = ("Busy", "NotAuthorized", "Unmanaged", "Preview", "NotEntitled", "SignInRequired", "NothingToDo",
          "NotOurs", "Inhibited", "InvalidArgument", "Failed")

_METHODS = """
    <!-- Fetch and verify the channel's update graph now and choose a target.
         Returns once the check has started; watch State. Downloads follow
         automatically when policy allows (unmetered, AC or battery above 30%).
         polkit: org.projectluma.update.check -->
    <method name="Check"/>
    <!-- Download and stage the chosen target now, including on a metered
         connection or low battery (the person asked). Returns once started.
         polkit: org.projectluma.update.download -->
    <method name="Download"/>
    <!-- Stop the running download, whether a person or the automatic policy
         started it. Nothing is staged or reported; State returns to
         available. Fails with NothingToDo when no download is running.
         Automatic downloads are also cancelled by the agent itself when the
         connection becomes metered or power runs low.
         polkit: org.projectluma.update.download -->
    <method name="Cancel"/>
    <!-- Restart into this agent's own staged update or rollback through
         logind, honouring its inhibitors. The caller must also be allowed
         org.freedesktop.login1.reboot, or
         org.freedesktop.login1.reboot-multiple-sessions when other people are
         logged in. Fails with NothingToDo when nothing waits for a restart,
         NotOurs when what waits was not prepared by this agent, Busy while an
         operation or rpm-ostree transaction runs, Inhibited when an inhibitor
         blocks restarting.
         polkit: org.projectluma.update.apply -->
    <method name="Apply"/>
    <!-- Follow another channel: stable, beta or nightly, all public, no
         enrollment. A more advanced channel is reached with the next update; a
         less advanced one waits until it catches up.
         polkit: org.projectluma.update.set-channel -->
    <method name="SetChannel">
      <arg type="s" name="channel" direction="in"/>
    </method>
    <!-- "Switch now": follow the channel and deploy its newest usable release
         immediately, even when that is older than the booted version.
         polkit: org.projectluma.update.set-channel -->
    <method name="SetChannelNow">
      <arg type="s" name="channel" direction="in"/>
    </method>
    <!-- Make the previous deployment the next boot; restart to finish.
         polkit: org.projectluma.update.rollback -->
    <method name="Rollback"/>
    <!-- Compatibility (every channel is public; SetChannel is enough).
         Get early updates: exchange the caller's Luma Connect device token for
         a preview credential from Hub, install it root-only, and follow
         beta or nightly. The token is used once and not stored. On a computer
         that follows no channel but is Adoptable, it then adopts the channel.
         SignInRequired when Hub no longer accepts the token; NotEntitled when
         the account may not have early updates.
         polkit: org.projectluma.update.enroll-preview -->
    <method name="EnrollPreview">
      <arg type="s" name="channel" direction="in"/>
      <arg type="s" name="connect_device_token" direction="in"/>
    </method>
    <!-- Leave early updates: remove the credential and follow stable.
         polkit: org.projectluma.update.enroll-preview -->
    <method name="LeavePreview"/>
    <!-- Clear the "went back to the previous version" notice.
         polkit: org.projectluma.update.check -->
    <method name="AcknowledgeRollback"/>
    <!-- Download updates in the background, or wait to be asked every time.
         Turning it off also stops an automatic download already running.
         Nothing is ever installed or restarted by this: only Apply() does that.
         polkit: org.projectluma.update.download -->
    <method name="SetAutomaticDownload">
      <arg type="b" name="enabled" direction="in"/>
    </method>
    <!-- Stop reminding about one version. It is still offered in Depot and is
         still listed as available; it is simply never downloaded on its own
         and never notified about again. A newer release is offered normally.
         polkit: org.projectluma.update.check -->
    <method name="IgnoreVersion">
      <arg type="s" name="version" direction="in"/>
    </method>
    <!-- Stop ignoring whichever version is ignored: the way back.
         polkit: org.projectluma.update.check -->
    <method name="ClearIgnoredVersion"/>
    <!-- Start following a Luma channel on a computer that follows none:
         rebase onto luma:luma/1/<arch>/<channel> and stage that channel's
         newest usable release, which may be older than what is booted.
         Offered only where Adoptable is true (the Luma remote and an
         update-graph key are installed and nothing else is staged); the graph
         must still verify. Nothing is applied until a person restarts, and the
         deployment they are running stays as the rollback. NothingToDo when
         the computer already follows a channel; Unmanaged with the reason when
         it cannot.
         polkit: org.projectluma.update.set-channel -->
    <method name="AdoptChannel">
      <arg type="s" name="channel" direction="in"/>
    </method>
    <!-- The timer's entry point (root only): check, download per policy, report. -->
    <method name="Automatic"/>
    <signal name="UpdateStaged">
      <arg type="s" name="version"/>
      <arg type="s" name="importance"/>
    </signal>
    <signal name="RolledBack">
      <arg type="s" name="failed_version"/>
      <arg type="s" name="current_version"/>
    </signal>
"""


def introspection_xml() -> str:
    properties = "".join(
        f'    <property name="{dbus_name(name)}" type="{signature}" access="read">\n'
        f'      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>\n'
        f'    </property>\n'
        for name, signature in DBUS_TYPES.items())
    return ('<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"\n'
            ' "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">\n'
            f'<node name="{OBJECT_PATH}">\n'
            f'  <interface name="{INTERFACE}">\n'
            f'{_METHODS}{properties}'
            '  </interface>\n'
            '</node>\n')
