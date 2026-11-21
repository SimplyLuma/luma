-- SPDX-License-Identifier: MPL-2.0
--
-- Project Luma per-app audio: pure helpers shared by luma/app-audio.lua.
-- No WirePlumber objects are touched here, so the module runs under a plain
-- Lua 5.4 interpreter in the tests.
--
-- The same rules exist in Python in luma_audio_devices/apps.py; the fixture
-- tests/luma-audio-devices/fixtures/app-keys.json is checked by both test
-- suites so the mixer, the capture sessions and the stored volumes always
-- agree about which app a stream belongs to.

local M = {}

-- The key every per-app decision is made with. The first present property
-- wins. The portal app id comes first because PipeWire sets it from
-- .flatpak-info, not from anything the app says about itself. The name comes
-- before the binary so that two apps sharing an interpreter (python3,
-- gst-launch-1.0) stay two apps.
M.KEY_PROPERTIES = {
  "pipewire.access.portal.app_id",
  "application.id",
  "application.name",
  "application.process.binary",
}

-- Streams that make up System sounds. pipewire-pulse maps libcanberra's
-- "event" to Notification; native clients may use either spelling.
M.SYSTEM_ROLES = {
  notification = true,
  event = true,
}

M.SYSTEM_KEY = "@system"

local function nonempty (value)
  if type (value) ~= "string" then
    return nil
  end
  value = value:match ("^%s*(.-)%s*$")
  if value == "" then
    return nil
  end
  return value
end

function M.truthy (value)
  return value == true or value == "true" or value == "1"
end

-- props: the stream node's properties; client: its client's properties (may
-- be nil). Returns key, source property name; or nil when the stream names
-- no app at all.
function M.app_key (props, client)
  props = props or {}
  client = client or {}
  for _, name in ipairs (M.KEY_PROPERTIES) do
    local value = nonempty (props [name])
    if not value and name == "pipewire.access.portal.app_id" then
      value = nonempty (client [name])
    end
    if not value and name == "application.process.binary" then
      value = nonempty (client [name])
    end
    if value then
      if name == "application.process.binary" then
        value = value:match ("([^/]+)$") or value
      end
      return value, name
    end
  end
  return nil, nil
end

function M.is_system_sound (props)
  local role = nonempty ((props or {}) ["media.role"])
  return role ~= nil and M.SYSTEM_ROLES [role:lower ()] == true
end

-- A stream the mixer lists and a capture can include: an app's playback
-- stream, never a monitor, a level meter or one of Luma's own nodes.
function M.is_app_playback (props)
  props = props or {}
  if props ["media.class"] ~= "Stream/Output/Audio" then
    return false
  end
  if M.truthy (props ["stream.monitor"]) then
    return false
  end
  if props ["luma.audio-levels"] or props ["luma.audio-capture.id"] then
    return false
  end
  -- The playback half of a loopback or filter is plumbing, not an app.
  if props ["node.link-group"] then
    return false
  end
  return true
end

-- A PipeWire remote xdg-desktop-portal opened for a sandboxed app
-- (OpenPipeWireRemote): it carries the portal's media roles, and it is not
-- the portal's own connection.
function M.is_portal_remote (client)
  client = client or {}
  if client ["pipewire.access.portal.media_roles"] == nil then
    return false
  end
  if client ["pipewire.access.portal.is_portal"] == "yes" then
    return false
  end
  return true
end

-- capture: { mode = "apps"|"system", apps = { [key] = true },
--            requester = "<app id>", include_self = bool }
function M.capture_includes (capture, key)
  if not capture or not key then
    return false
  end
  if capture.mode == "apps" then
    return capture.apps [key] == true
  end
  if capture.mode == "system" then
    if not capture.include_self and capture.requester and
        capture.requester ~= "" and key == capture.requester then
      return false
    end
    return true
  end
  return false
end

-- Turn a decoded capture request (from metadata JSON) into the table
-- capture_includes() reads. Unknown modes are refused.
function M.capture_from_request (request)
  if type (request) ~= "table" then
    return nil, "not an object"
  end
  local mode = request.mode
  if mode ~= "apps" and mode ~= "system" then
    return nil, "unknown mode"
  end
  local apps = {}
  if type (request.apps) == "table" then
    for _, key in ipairs (request.apps) do
      if type (key) == "string" and key ~= "" then
        apps [key] = true
      end
    end
  end
  if mode == "apps" and next (apps) == nil then
    return nil, "no apps chosen"
  end
  return {
    mode = mode,
    apps = apps,
    requester = type (request.requester) == "string" and request.requester or "",
    include_self = request.include_self == true,
    owner = tonumber (request.owner),
    name = type (request.name) == "string" and request.name or "",
  }, nil
end

-- The mixer's 0..1 scale is cubic, like wpctl and GNOME's sliders.
function M.cubic_to_linear (value)
  value = tonumber (value) or 0
  if value < 0 then
    value = 0
  end
  return value * value * value
end

function M.linear_to_cubic (value)
  value = tonumber (value) or 0
  if value <= 0 then
    return 0
  end
  return value ^ (1 / 3)
end

-- The loudest channel stands for the stream, as pavucontrol and GNOME do.
function M.stream_volume (channel_volumes)
  local loudest = nil
  for _, v in ipairs (channel_volumes or {}) do
    v = tonumber (v)
    if v and (loudest == nil or v > loudest) then
      loudest = v
    end
  end
  return loudest
end

return M
