-- SPDX-License-Identifier: MPL-2.0
--
-- Project Luma output policy: pure helpers shared by
-- luma/output-policy.lua. No WirePlumber objects are touched here so the
-- module can be tested with a plain Lua interpreter.
--
-- The same rules exist in Python in luma_audio_devices/classify.py; the
-- fixture tests/luma-audio-devices/fixtures/device-keys.json is checked by both
-- test suites so the two can never disagree about a device key.

local M = {}

-- Node names chosen by PipeWire's network modules when nobody overrides them
-- (module-raop-discover, module-zeroconf-discover, module-rtp-session,
-- module-rtp-sink, module-roc-sink, module-snapcast-discover,
-- module-netjack2-manager) and by Luma's own AirPlay service.
M.NETWORK_NAME_PREFIXES = {
  "raop_sink.", "luma_airplay.", "tunnel.", "rtp_session.", "rtp-sink",
  "roc-sink", "snapcast", "netjack2_",
}

-- Properties only network transports set.
M.NETWORK_PROPERTIES = {
  "raop.ip", "raop.hostname", "tunnel.mode", "pulse.server.address",
  "rtp.destination.ip", "snapcast.name", "roc.remote.source.endpoint",
  "luma.airplay.id",
}

function M.truthy (value)
  return value == true or value == "true" or value == "1"
end

local function starts_with (text, prefix)
  return type (text) == "string" and text:sub (1, #prefix) == prefix
end

local function nonempty (value)
  if value == nil then
    return nil
  end
  value = tostring (value)
  if value == "" then
    return nil
  end
  return value
end

function M.is_network (props)
  if M.truthy (props ["node.network"]) then
    return true
  end
  if props ["sess.media"] == "raop" then
    return true
  end
  for _, key in ipairs (M.NETWORK_PROPERTIES) do
    if nonempty (props [key]) then
      return true
    end
  end
  local name = props ["node.name"]
  for _, prefix in ipairs (M.NETWORK_NAME_PREFIXES) do
    if starts_with (name, prefix) then
      return true
    end
  end
  return false
end

-- Stable, person-meaningful identity of the device behind a node.
-- props: node properties laid over the properties of its device.
-- route_info: the info dictionary of the node's active route, or nil.
function M.device_key (props, route_info)
  route_info = route_info or {}

  if M.is_network (props) then
    local airplay = nonempty (props ["luma.airplay.id"])
    if airplay then
      return "airplay:" .. airplay:lower ()
    end
    return "network:" .. (props ["node.name"] or "")
  end

  local address = nonempty (props ["api.bluez5.address"])
  if address then
    return "bluez:" .. address:upper ()
  end

  if props ["device.bus"] == "usb" then
    local vendor = nonempty (props ["device.vendor.id"])
    local product = nonempty (props ["device.product.id"])
    if vendor and product then
      local key = "usb:" .. vendor:lower () .. ":" .. product:lower ()
      local serial = nonempty (props ["device.serial"])
      if serial then
        key = key .. ":" .. serial
      end
      return key
    end
  end

  local card = nonempty (props ["device.bus-path"]) or nonempty (props ["device.name"])
  local port_type = route_info ["port.type"]
  if port_type == "hdmi" or props ["device.icon_name"] == "video-display" then
    local product = nonempty (route_info ["device.product.name"])
    return "display:" .. (card or "") .. ":" .. (product or props ["node.name"] or "")
  end

  if card then
    local slot = nonempty (props ["card.profile.device"]) or props ["node.name"] or ""
    return "card:" .. card .. ":" .. slot
  end

  return "node:" .. (props ["node.name"] or "")
end

-- Route "info" arrives either as a dictionary or as PipeWire's flat
-- { count, key, value, key, value, ... } array. Always hand back a dictionary.
function M.route_info_table (info)
  local result = {}
  if type (info) ~= "table" then
    return result
  end
  if info [1] ~= nil then
    local start = 1
    if type (info [1]) == "number" then
      start = 2
    end
    local i = start
    while info [i] ~= nil and info [i + 1] ~= nil do
      result [tostring (info [i])] = info [i + 1]
      i = i + 2
    end
    return result
  end
  for k, v in pairs (info) do
    result [k] = v
  end
  return result
end

-- Decide which candidate nodes the stock selection hooks may look at.
-- nodes:   array of property tables (select-default-node "available-nodes")
-- context: {
--   armed       = set of node names the person chose while they were present,
-- }
-- Returns the kept array and an array of { name, reason } for logging.
function M.filter_candidates (nodes, context)
  local kept, removed = {}, {}
  local armed = context.armed or {}
  for _, props in ipairs (nodes) do
    local name = props ["node.name"]
    local reason = nil
    if not armed [name] then
      if M.is_network (props) then
        reason = "network output that was not chosen while it was present"
      end
    end
    if reason then
      table.insert (removed, { name = name, reason = reason })
    else
      table.insert (kept, props)
    end
  end
  return kept, removed
end

-- Explicit preferences score at least 20000 in the stock hooks (the configured
-- node 30000+, previously configured nodes 20001-i+priority); plain
-- priority.session values stay far below. Anything under this threshold was
-- picked only because a node advertises a higher priority.
M.PREFERENCE_THRESHOLD = 15000

-- A newly appearing node must not silently take over from a default that is
-- still present just because its priority.session is higher. When the default
-- itself went away, the sound returns to the most recent earlier default that
-- is still present (the speaker it played from before an AirPlay session, say)
-- rather than to whichever node advertises the highest priority.
-- recent: node names that were the effective default, newest first.
-- Returns the node to apply.
function M.keep_current (selected, selected_priority, recent, available_names)
  if selected == nil then
    return selected
  end
  if (selected_priority or 0) >= M.PREFERENCE_THRESHOLD then
    return selected
  end
  for _, name in ipairs (recent or {}) do
    if name == selected then
      return selected
    end
    if available_names [name] then
      return name
    end
  end
  return selected
end

-- Put name at the front of a recent-defaults list, without duplicates, keeping
-- at most limit entries.
function M.push_recent (recent, name, limit)
  local result = { name }
  for _, entry in ipairs (recent or {}) do
    if entry ~= name and #result < (limit or 8) then
      table.insert (result, entry)
    end
  end
  return result
end

return M
