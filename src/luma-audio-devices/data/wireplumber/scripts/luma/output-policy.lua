-- SPDX-License-Identifier: MPL-2.0
--
-- Project Luma output policy for WirePlumber 0.5 default-node selection.
--
-- The stock default-nodes hooks pick, in order: the configured default
-- (default.configured.*), previously configured defaults, then the node with
-- the highest priority.session. Luma keeps that model and adds two rules:
--
-- 1. A network output (AirPlay, pulse tunnel, RTP, ROC, Snapcast, ...) is never
--    selected automatically. It becomes the default only while it is present
--    and the person chose it during that presence. When it disappears the
--    choice lapses, so a speaker in another room never takes the sound back
--    when it is rediscovered later.
-- 2. A node picked only by its priority.session never displaces a default
--    that is still present, and when the default goes away the sound returns
--    to the previous default that is still present. A display, dock or USB
--    sound card that appears keeps the sound where it is; luma-audio-devices
--    switches to headphones and Bluetooth audio the person connects.
--
-- Nodes whose routes are unavailable (an HDMI port without a display) are
-- already excluded by the stock rescan hook before any of this runs.

log = Log.open_topic ("s-luma-output-policy")

policy = require ("luma-output-policy")

-- node.name -> true while the node is present and was chosen during presence
armed = {}

-- default-node type -> node names that were the effective default, newest
-- first. Runtime only: after a restart the stock hooks start from a clean slate.
recent_defaults = {}

local function linkable_present (source, name)
  local si_om = source:call ("get-object-manager", "session-item")
  return si_om:lookup {
    type = "SiLinkable",
    Constraint { "node.name", "=", name },
  } ~= nil
end

-- The person (or luma-audio-devices, for headphones they connected) chose a
-- default. Arm it if it exists right now.
SimpleEventHook {
  name = "luma/default-nodes/arm-explicit-choice",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "metadata-changed" },
      Constraint { "metadata.name", "=", "default" },
      Constraint { "event.subject.key", "c", "default.configured.audio.sink",
          "default.configured.audio.source" },
    },
  },
  execute = function (event)
    local props = event:get_properties ()
    local value = props ["event.subject.value"]
    if not value then
      return
    end
    local ok, parsed = pcall (function () return Json.Raw (value):parse () end)
    local name = ok and type (parsed) == "table" and parsed.name or nil
    if not name then
      return
    end
    if linkable_present (event:get_source (), name) then
      armed [name] = true
      log:info ("explicit choice while present: " .. name)
    end
  end
}:register ()

SimpleEventHook {
  name = "luma/default-nodes/disarm-removed",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "session-item-removed" },
      Constraint { "event.session-item.interface", "=", "linkable" },
    },
  },
  execute = function (event)
    local props = event:get_properties ()
    local name = props ["node.name"]
    if name and armed [name] then
      armed [name] = nil
      log:info ("choice lapsed with the node: " .. name)
    end
  end
}:register ()

SimpleEventHook {
  name = "luma/default-nodes/filter-candidates",
  before = { "default-nodes/find-selected-default-node",
             "default-nodes/find-stored-default-node",
             "default-nodes/find-best-default-node" },
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "select-default-node" },
      Constraint { "default-node.type", "c", "audio.sink", "audio.source" },
    },
  },
  execute = function (event)
    local available = event:get_data ("available-nodes")
    available = available and available:parse ()
    if not available then
      return
    end

    local props = event:get_properties ()
    local def_node_type = props ["default-node.type"]
    local kept, removed = policy.filter_candidates (available, { armed = armed })

    if #removed == 0 then
      return
    end
    for _, entry in ipairs (removed) do
      log:info (string.format ("%s: not a candidate: %s (%s)",
          def_node_type, tostring (entry.name), entry.reason))
    end

    local objects = {}
    for _, node_props in ipairs (kept) do
      table.insert (objects, Json.Object (node_props))
    end
    event:set_data ("available-nodes", Json.Array (objects))
  end
}:register ()

SimpleEventHook {
  name = "luma/default-nodes/remember-effective-default",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "metadata-changed" },
      Constraint { "metadata.name", "=", "default" },
      Constraint { "event.subject.key", "c", "default.audio.sink", "default.audio.source" },
    },
  },
  execute = function (event)
    local props = event:get_properties ()
    local def_node_type = props ["event.subject.key"]:sub (9)
    local value = props ["event.subject.value"]
    if not value then
      return
    end
    local ok, parsed = pcall (function () return Json.Raw (value):parse () end)
    local name = ok and type (parsed) == "table" and parsed.name or nil
    if name then
      recent_defaults [def_node_type] =
          policy.push_recent (recent_defaults [def_node_type], name, 8)
    end
  end
}:register ()

SimpleEventHook {
  name = "luma/default-nodes/keep-current",
  after = { "default-nodes/find-selected-default-node",
            "default-nodes/find-stored-default-node",
            "default-nodes/find-best-default-node" },
  before = "default-nodes/apply-default-node",
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "select-default-node" },
      Constraint { "default-node.type", "c", "audio.sink", "audio.source" },
    },
  },
  execute = function (event)
    local selected = event:get_data ("selected-node")
    local priority = event:get_data ("selected-node-priority") or 0
    if not selected then
      return
    end

    local props = event:get_properties ()
    local def_node_type = props ["default-node.type"]

    local names = {}
    local available = event:get_data ("available-nodes")
    available = available and available:parse () or {}
    for _, node_props in ipairs (available) do
      names [node_props ["node.name"]] = true
    end

    local chosen = policy.keep_current (selected, priority,
        recent_defaults [def_node_type], names)
    if chosen ~= selected then
      log:info (string.format ("%s: staying with %s; %s would win only by priority",
          def_node_type, chosen, selected))
      event:set_data ("selected-node", chosen)
    end
  end
}:register ()
