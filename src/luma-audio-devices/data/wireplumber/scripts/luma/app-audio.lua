-- SPDX-License-Identifier: MPL-2.0
--
-- Project Luma per-app audio for WirePlumber 0.5.
--
-- Three jobs, all keyed by the app a stream belongs to (lib/luma-app-audio.lua
-- app_key):
--
-- 1. Per-app volume, mute and output, remembered across restarts.
--    The stock node/restore-stream hook keys streams by media.role first, so
--    every SDL game ("Game") or GStreamer music player ("Music") shares one
--    remembered volume. This hook runs after it and restores what was saved
--    for the app itself. System sounds keep the stock route-settings key.
--
-- 2. Capture sessions for the audio capture portal. A capture is one
--    support.null-audio-sink node, class Audio/Source/Virtual/Capture (linked
--    like a virtual source, never listed by pipewire-pulse, so it is never
--    offered as a microphone), fed by every stream of the chosen apps; a new
--    stream from one of those apps is linked as soon as it appears.
--
-- 3. A guard for portal-restricted PipeWire remotes. Such a client can see
--    only the nodes the portal granted, but the stock linking hooks would
--    still connect its capture streams to the default microphone. A capture
--    stream from a portal remote links only to a capture node issued to the
--    same app, and is refused otherwise.
--
-- Control is through the "luma-app-audio" metadata (written by
-- luma-audio-devices):
--   app.volume:<key>  {"volume": <linear>, "mute": <bool>}  (either optional)
--   app.target:<key>  {"name": "<node.name>"} or {"name": null}
--   capture:<id>      {"mode": "apps"|"system", "apps": [...], "requester":
--                      "<app id>", "include_self": bool, "owner": <client id>,
--                      "name": "<label>"}; removing the key ends the capture
-- Commands are removed from the metadata once applied; captures stay while
-- they are live, and are dropped when their owner leaves PipeWire.
--
-- To turn this off for one account, add a fragment in
-- ~/.config/wireplumber/wireplumber.conf.d/ with:
--   wireplumber.profiles = { main = { luma.app-audio = disabled } }

log = Log.open_topic ("s-luma-app-audio")

app = require ("luma-app-audio")
cutils = require ("common-utils")
lutils = require ("linking-utils")

METADATA_NAME = "luma-app-audio"
CAPTURE_CLASS = "Audio/Source/Virtual/Capture"
ROUTE_SETTINGS_SYSTEM_KEY = "restore.stream.Output/Audio.media.role:Notification"
-- A restore is confirmed by the node reporting the restored Props. Until then
-- (at most this long) Props changes are the node's initial state, not a
-- change worth saving.
RESTORE_SETTLE_USEC = 2000000

state = State ("luma-app-audio")
state_table = state:load ()

metadata = ImplMetadata (METADATA_NAME)
metadata:activate (Features.ALL, function (m, e)
  if e then
    log:warning ("cannot create the " .. METADATA_NAME .. " metadata: " .. tostring (e))
  else
    log:info ("ready")
  end
end)

-- node bound-id -> { volume = v, mute = m, until = usec }
restoring = {}
-- capture id -> { spec = ..., node = proxy, links = { [stream si id] = silink } }
captures = {}
-- portal-remote stream si ids already refused (log once)
refused = {}

-- Helpers -------------------------------------------------------------------

local function client_props (node)
  local client_id = node.properties ["client.id"]
  if not client_id then
    return nil
  end
  local client = cutils.get_object_manager ("client"):lookup {
    Constraint { "bound-id", "=", client_id, type = "gobject" },
  }
  return client and client.properties or nil
end

local function key_for (node)
  return app.app_key (node.properties, client_props (node))
end

local function load_app (key)
  local value = state_table [key]
  if not value then
    return {}
  end
  local json = Json.Raw (value)
  if not json or not json:is_object () then
    return {}
  end
  return json:parse ()
end

local function save_app (key, saved)
  local out = {}
  if saved.volume ~= nil then out.volume = saved.volume end
  if saved.mute ~= nil then out.mute = saved.mute end
  if saved.target ~= nil then out.target = saved.target end
  state_table [key] = Json.Object (out):to_string ()
  state:save_after_timeout (state_table)
end

local function parse_json (value)
  if not value then
    return nil
  end
  local ok, parsed = pcall (function ()
    local json = Json.Raw (value)
    if not json or not json:is_object () then
      return nil
    end
    return json:parse ()
  end)
  return ok and parsed or nil
end

local function channel_count (node)
  for pod in node:iterate_params ("Format") do
    local parsed = pod:parse ()
    if parsed ~= nil then
      local channels = parsed.properties.channels
      if type (channels) == "number" then
        return channels
      elseif type (channels) == "table" and #channels > 0 then
        return channels [1]
      end
    end
  end
  return 2
end

local function node_volume (node)
  for p in node:iterate_params ("Props") do
    local props = cutils.parseParam (p, "Props")
    if props then
      return app.stream_volume (props.channelVolumes), props.mute
    end
  end
  return nil, nil
end

-- Volume goes through WirePlumber's mixer API, the path wpctl takes: it
-- keeps the node's channel layout and current values. A Props update built
-- here and sent from a metadata hook left a running stream unable to answer
-- parameter requests, stalling every client that enumerates the graph
-- (pw-dump, wpctl). The API's scale is linear; nothing else in WirePlumber
-- changes it.
local function apply_volume (node, volume, mute)
  local mixer = Plugin.find ("mixer-api")
  if mixer then
    local value = {}
    if volume ~= nil then
      value.volume = volume + 0.0
    end
    if mute ~= nil then
      value.mute = mute
    end
    mixer:call ("set-volume", node ["bound-id"], value)
    return
  end
  -- Without the mixer API (a stripped configuration), a plain Props update.
  local current_volume, current_mute = node_volume (node)
  local volumes = { "Spa:Float" }
  for _ = 1, channel_count (node) do
    table.insert (volumes, volume or current_volume or 1.0)
  end
  node:set_param ("Props", Pod.Object {
    "Spa:Pod:Object:Param:Props", "Props",
    mute = (mute == nil) and (current_mute == true) or mute,
    channelVolumes = Pod.Array (volumes),
  })
end

local function app_streams (key)
  local found = {}
  for node in cutils.get_object_manager ("node"):iterate {
    Constraint { "media.class", "=", "Stream/Output/Audio" },
  } do
    if app.is_app_playback (node.properties) then
      local node_key = key_for (node)
      if key == app.SYSTEM_KEY then
        if app.is_system_sound (node.properties) then
          table.insert (found, node)
        end
      elseif node_key == key and not app.is_system_sound (node.properties) then
        table.insert (found, node)
      end
    end
  end
  return found
end

local function set_target (node, target_name)
  local default = cutils.get_default_metadata_object ()
  if not default then
    return
  end
  local id = node ["bound-id"]
  if not target_name then
    default:set (id, "target.object", nil, nil)
    return
  end
  local target = cutils.get_object_manager ("node"):lookup {
    Constraint { "node.name", "=", target_name },
  }
  if target then
    default:set (id, "target.object", "Spa:Id", target.properties ["object.serial"])
  end
end

-- 1. Remembered per-app volume, mute and output ------------------------------

SimpleEventHook {
  name = "luma/app-audio/restore",
  after = "node/restore-stream",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "node-added" },
      Constraint { "media.class", "=", "Stream/Output/Audio" },
    },
  },
  execute = function (event)
    local node = event:get_subject ()
    local props = node.properties
    if not app.is_app_playback (props) or app.is_system_sound (props) then
      return
    end
    if props ["state.restore-props"] == "false" then
      return
    end
    local key = key_for (node)
    if not key then
      return
    end
    local saved = load_app (key)
    if saved.volume ~= nil or saved.mute ~= nil then
      log:info (node, "restoring " .. key .. ": volume " .. tostring (saved.volume) ..
          ", mute " .. tostring (saved.mute))
      restoring [node ["bound-id"]] = {
        volume = saved.volume, mute = saved.mute,
        ["until"] = GLib.get_monotonic_time () + RESTORE_SETTLE_USEC,
      }
      apply_volume (node, saved.volume, saved.mute)
    end
    if saved.target and props ["state.restore-target"] ~= "false" and
        not props ["target.object"] and not props ["node.target"] then
      log:info (node, "restoring output of " .. key .. ": " .. saved.target)
      set_target (node, saved.target)
    end
  end
}:register ()

SimpleEventHook {
  name = "luma/app-audio/store-volume",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "node-params-changed" },
      Constraint { "event.subject.param-id", "=", "Props" },
      Constraint { "media.class", "=", "Stream/Output/Audio" },
    },
  },
  execute = function (event)
    local node = event:get_subject ()
    local props = node.properties
    if not app.is_app_playback (props) or app.is_system_sound (props) then
      return
    end
    local volume, mute = node_volume (node)
    if volume == nil and mute == nil then
      return
    end
    local id = node ["bound-id"]
    local pending = restoring [id]
    if pending then
      local settled = (pending.volume == nil or (volume and math.abs (volume - pending.volume) < 1e-4)) and
          (pending.mute == nil or pending.mute == mute)
      if settled or GLib.get_monotonic_time () > pending ["until"] then
        restoring [id] = nil
      end
      if not settled then
        return
      end
    end
    local key = key_for (node)
    if not key then
      return
    end
    local saved = load_app (key)
    if saved.volume ~= nil and volume ~= nil and math.abs (saved.volume - volume) < 1e-4 and
        saved.mute == mute then
      return
    end
    saved.volume = volume
    saved.mute = mute
    save_app (key, saved)
  end
}:register ()

SimpleEventHook {
  name = "luma/app-audio/store-target",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "metadata-changed" },
      Constraint { "metadata.name", "=", "default" },
      Constraint { "event.subject.key", "=", "target.object" },
    },
  },
  execute = function (event)
    local props = event:get_properties ()
    local node = cutils.get_object_manager ("node"):lookup {
      Constraint { "bound-id", "=", props ["event.subject.id"], type = "gobject" },
    }
    if not node or not app.is_app_playback (node.properties) or
        app.is_system_sound (node.properties) then
      return
    end
    local key = key_for (node)
    if not key then
      return
    end
    local value = props ["event.subject.value"]
    local target_name = nil
    if value and value ~= "-1" then
      local target = cutils.get_object_manager ("node"):lookup {
        Constraint { "object.serial", "=", value, type = "pw-global" },
      }
      target_name = target and target.properties ["node.name"] or nil
    end
    local saved = load_app (key)
    if saved.target == target_name then
      return
    end
    saved.target = target_name
    save_app (key, saved)
  end
}:register ()

restoring_cleanup = SimpleEventHook {
  name = "luma/app-audio/forget-node",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "node-removed" },
      Constraint { "media.class", "=", "Stream/Output/Audio" },
    },
  },
  execute = function (event)
    -- A removed node is no longer bound; its global id is in its properties.
    local id = tonumber (event:get_subject ().properties ["object.id"])
    if id then
      restoring [id] = nil
    end
  end
}
restoring_cleanup:register ()

-- Commands from luma-audio-devices -------------------------------------------

local function command_done (key)
  -- Remove the command so a stale value is never applied twice.
  metadata:set (0, key, nil, nil)
end

local function set_app_volume (key, request)
  local volume = tonumber (request.volume)
  local mute = request.mute
  if mute ~= nil then
    mute = mute == true
  end
  if key == app.SYSTEM_KEY then
    -- System sounds: the same route-settings value pipewire-pulse and the
    -- stock state-stream hook read, so it holds for sounds not yet played.
    local route = cutils.get_object_manager ("metadata"):lookup {
      Constraint { "metadata.name", "=", "route-settings" },
    }
    if route then
      local value = {}
      if volume ~= nil then value.volume = volume end
      if mute ~= nil then value.mute = mute end
      route:set (0, ROUTE_SETTINGS_SYSTEM_KEY, "Spa:String:JSON", Json.Object (value):to_string ())
    end
  end
  for _, node in ipairs (app_streams (key)) do
    apply_volume (node, volume, mute)
  end
  if key ~= app.SYSTEM_KEY then
    local saved = load_app (key)
    if volume ~= nil then saved.volume = volume end
    if mute ~= nil then saved.mute = mute end
    save_app (key, saved)
  end
end

local function set_app_target (key, request)
  local name = request.name
  if type (name) ~= "string" or name == "" then
    name = nil
  end
  for _, node in ipairs (app_streams (key)) do
    set_target (node, name)
  end
  if key ~= app.SYSTEM_KEY then
    local saved = load_app (key)
    saved.target = name
    save_app (key, saved)
  end
end

-- 2. Capture sessions ---------------------------------------------------------

local function capture_si (capture)
  if not capture.node or not capture.node ["bound-id"] then
    return nil
  end
  return cutils.get_object_manager ("session-item"):lookup {
    type = "SiLinkable",
    Constraint { "node.id", "=", tostring (capture.node ["bound-id"]) },
  }
end

local function link_into (capture, stream_si)
  local target = capture_si (capture)
  if not target or capture.links [stream_si.id] then
    return
  end
  local silink = SessionItem ("si-standard-link")
  if not silink:configure {
    ["out.item"] = stream_si,
    ["in.item"] = target,
    ["out.item.port.context"] = "output",
    ["in.item.port.context"] = "input",
    ["passthrough"] = false,
    ["exclusive"] = false,
    ["luma.audio-capture.id"] = capture.id,
  } then
    log:warning ("cannot configure a capture link for " .. capture.id)
    return
  end
  capture.links [stream_si.id] = silink
  silink:register ()
  silink:activate (Feature.SessionItem.ACTIVE, function (l, e)
    if e then
      log:warning (l, "capture link failed: " .. tostring (e))
      capture.links [stream_si.id] = nil
      l:remove ()
    end
  end)
end

local function stream_matches (capture, stream_si)
  local node = stream_si:get_associated_proxy ("node")
  if not node or not app.is_app_playback (node.properties) then
    return false
  end
  local key = key_for (node)
  if app.is_system_sound (node.properties) then
    -- System sounds are part of "all sound", and can be chosen by name.
    return capture.mode == "system" or capture.apps [app.SYSTEM_KEY] == true
  end
  return app.capture_includes (capture, key)
end

local function link_matching_streams (capture)
  for stream_si in cutils.get_object_manager ("session-item"):iterate {
    type = "SiLinkable",
    Constraint { "media.class", "=", "Stream/Output/Audio" },
  } do
    if stream_matches (capture, stream_si) then
      link_into (capture, stream_si)
    end
  end
end

local function end_capture (id, reason)
  local capture = captures [id]
  if not capture then
    return
  end
  captures [id] = nil
  for _, silink in pairs (capture.links) do
    silink:remove ()
  end
  capture.links = {}
  if capture.node then
    capture.node:request_destroy ()
  end
  capture.node = nil
  log:info ("capture " .. id .. " ended: " .. reason)
end

local function start_capture (id, request)
  local spec, why = app.capture_from_request (request)
  if not spec then
    log:warning ("capture " .. id .. " refused: " .. why)
    return
  end
  end_capture (id, "replaced")
  local capture = { id = id, spec = spec, links = {}, mode = spec.mode,
                    apps = spec.apps, requester = spec.requester,
                    include_self = spec.include_self, owner = spec.owner }
  captures [id] = capture
  local node = Node ("adapter", {
    ["factory.name"] = "support.null-audio-sink",
    ["node.name"] = "luma-audio-capture." .. id,
    ["node.description"] = spec.name ~= "" and spec.name or "App audio capture",
    ["media.class"] = CAPTURE_CLASS,
    ["audio.position"] = "FL,FR",
    ["node.virtual"] = true,
    ["node.passive"] = true,
    ["object.linger"] = false,
    ["luma.audio-capture.id"] = id,
    ["luma.audio-capture.app-id"] = spec.requester,
  })
  capture.node = node
  node:activate (Feature.Proxy.BOUND, function (n, e)
    if e then
      log:warning ("capture " .. id .. " node failed: " .. tostring (e))
      end_capture (id, "node failed")
      return
    end
    log:info ("capture " .. id .. " is node " .. tostring (n ["bound-id"]) ..
        " (" .. spec.mode .. ") for " .. (spec.requester ~= "" and spec.requester or "a host app"))
  end)
end

SimpleEventHook {
  name = "luma/app-audio/commands",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "metadata-changed" },
      Constraint { "metadata.name", "=", METADATA_NAME },
    },
  },
  execute = function (event)
    local props = event:get_properties ()
    local key = props ["event.subject.key"]
    local value = props ["event.subject.value"]
    if not key then
      return
    end
    local kind, rest = key:match ("^([%w%.]+):(.+)$")
    if not kind then
      return
    end
    if kind == "capture" then
      if value == nil then
        end_capture (rest, "closed")
      else
        local request = parse_json (value)
        if request then
          start_capture (rest, request)
        end
      end
      return
    end
    if value == nil then
      return
    end
    local request = parse_json (value)
    if not request then
      command_done (key)
      return
    end
    if kind == "app.volume" then
      set_app_volume (rest, request)
    elseif kind == "app.target" then
      set_app_target (rest, request)
    end
    command_done (key)
  end
}:register ()

-- A capture node's item, or a new app stream: link what matches.
SimpleEventHook {
  name = "luma/app-audio/capture-linkable-added",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "session-item-added" },
      Constraint { "event.session-item.interface", "=", "linkable" },
    },
  },
  execute = function (event)
    local si = event:get_subject ()
    local props = si.properties
    local capture_id = props ["luma.audio-capture.id"]
    if capture_id and captures [capture_id] then
      link_matching_streams (captures [capture_id])
      return
    end
    if props ["media.class"] ~= "Stream/Output/Audio" then
      return
    end
    for _, capture in pairs (captures) do
      if stream_matches (capture, si) then
        link_into (capture, si)
      end
    end
  end
}:register ()

SimpleEventHook {
  name = "luma/app-audio/capture-linkable-removed",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "session-item-removed" },
      Constraint { "event.session-item.interface", "=", "linkable" },
    },
  },
  execute = function (event)
    local si = event:get_subject ()
    refused [si.id] = nil
    -- linking/linkable-removed removes the links themselves.
    for _, capture in pairs (captures) do
      capture.links [si.id] = nil
    end
  end
}:register ()

SimpleEventHook {
  name = "luma/app-audio/capture-owner-removed",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "client-removed" },
    },
  },
  execute = function (event)
    local client_id = tonumber (event:get_subject ().properties ["object.id"])
    if not client_id then
      return
    end
    for id, capture in pairs (captures) do
      if capture.owner and capture.owner == client_id then
        end_capture (id, "its owner left")
        metadata:set (0, "capture:" .. id, nil, nil)
      end
    end
  end
}:register ()

-- 3. Portal remotes only link to their own capture ----------------------------

local function capture_for_target (wanted, app_id)
  if not wanted then
    return nil
  end
  for si in cutils.get_object_manager ("session-item"):iterate {
    type = "SiLinkable",
    Constraint { "media.class", "=", CAPTURE_CLASS },
  } do
    local props = si.properties
    if (props ["object.serial"] == wanted or props ["node.name"] == wanted or
        props ["node.id"] == wanted) and
        (props ["luma.audio-capture.app-id"] or "") == (app_id or "") then
      return si
    end
  end
  return nil
end

SimpleEventHook {
  name = "luma/app-audio/portal-remote-targets",
  before = "linking/find-defined-target",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "select-target" },
    },
  },
  execute = function (event)
    local _, _, si, si_props, si_flags, target =
        lutils:unwrap_select_target_event (event)
    if si_props ["media.type"] ~= "Audio" or si_props ["item.node.direction"] ~= "input" or
        si_props ["item.node.type"] ~= "stream" then
      return
    end
    local node = si:get_associated_proxy ("node")
    local client = node and client_props (node)
    if not client or not app.is_portal_remote (client) then
      return
    end
    local app_id = client ["pipewire.access.portal.app_id"] or ""
    local wanted = si_props ["target.object"] or si_props ["node.target"]
    local allowed = capture_for_target (wanted, app_id)
    if allowed then
      si_flags.has_defined_target = true
      si_flags.has_node_defined_target = true
      event:set_data ("target", allowed)
      return
    end
    if not refused [si.id] then
      refused [si.id] = true
      log:notice (si, "refused a capture stream of portal client " ..
          (app_id ~= "" and app_id or "(host)") .. " to " .. tostring (wanted))
      lutils.sendClientError (event, node, -1, "not permitted to record this")
    end
    event:stop_processing ()
  end
}:register ()
