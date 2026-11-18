-- SPDX-License-Identifier: Apache-2.0
-- Touch Lab shadow probe: proves the deployment path, not a classifier.
-- Marks any touchpad contact that lands in the bottom 15% of the pad as a
-- palm by rewriting its ABS_MT_TOOL_TYPE, the same way a trained model will,
-- and reports how long its frame callback takes. Run it only in a shadow
-- libinput (touchlab recorder --shadow-plugins DIR), never in the compositor.
libinput:register({1})

libinput:connect("new-evdev-device", function(device)
    local props = device:udev_properties()
    if props.ID_INPUT_TOUCHPAD ~= "1" then
        return
    end
    local ymax = device:absinfos()[evdev.ABS_MT_POSITION_Y].maximum
    local limit = ymax * 0.85
    local slot, marked, frames, spent = 0, {}, 0, 0
    device:connect("evdev-frame", function(_device, frame, timestamp)
        local t0 = libinput:now()
        local out, changed = {}, false
        for _, e in ipairs(frame) do
            out[#out + 1] = e
            if e.usage == evdev.ABS_MT_SLOT then
                slot = e.value
            elseif e.usage == evdev.ABS_MT_TRACKING_ID and e.value < 0 then
                marked[slot] = nil
            elseif e.usage == evdev.ABS_MT_POSITION_Y and e.value > limit and not marked[slot] then
                marked[slot] = true
                out[#out + 1] = { usage = evdev.ABS_MT_TOOL_TYPE, value = 2 }
                changed = true
                libinput:log_info(string.format("touchlab-probe: slot %d palm at %d", slot, timestamp))
            end
        end
        frames = frames + 1
        spent = spent + (libinput:now() - t0)
        if frames % 500 == 0 then
            libinput:log_info(string.format("touchlab-probe: %d frames, %d us in callbacks", frames, spent))
        end
        if changed then
            return out
        end
    end)
end)
