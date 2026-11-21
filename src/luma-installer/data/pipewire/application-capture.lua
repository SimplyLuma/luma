-- SPDX-License-Identifier: Apache-2.0
--
-- Installed applications play sound on their PipeWire socket; they do not
-- record on it.
--
-- Installed as /usr/share/wireplumber/scripts/luma/application-capture.lua by
-- luma-application-installer and loaded by 70-luma-application-access.conf.
--
-- A client on the application socket (access "luma-application") sees the
-- sound outputs so it can play to them. An output also has a monitor, and
-- WirePlumber links a capture stream to the monitor of any output the client
-- can see: the application would record everything the computer plays. This
-- hook runs before a target is chosen and leaves such a stream unlinked,
-- telling the client it was refused. Microphones are not visible on that
-- socket at all; cameras and screens come from the portal on a connection of
-- their own, which this hook never sees as "luma-application".

lutils = require ("linking-utils")
log = Log.open_topic ("s-luma-application")

SimpleEventHook {
  name = "luma/application-capture",
  before = "linking/find-defined-target",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "select-target" },
    },
  },
  execute = function (event)
    local source, _, si, si_props = lutils:unwrap_select_target_event (event)
    if si_props ["item.node.direction"] ~= "input" then
      return
    end

    local node = si:get_associated_proxy ("node")
    local client_id = node and node.properties ["client.id"]
    if not client_id then
      return
    end

    local clients_om = source:call ("get-object-manager", "client")
    local client = clients_om:lookup {
      Constraint { "bound-id", "=", client_id, type = "gobject" }
    }
    if not client or client.properties ["pipewire.access"] ~= "luma-application" then
      return
    end

    log:info (si, string.format ("capture refused on the application socket: %s",
        tostring (si_props ["node.name"])))
    event:set_data ("target", nil)
    client:send_error (node ["bound-id"], -13, "capture is not available on this socket")
    event:stop_processing ()
  end
}:register ()
