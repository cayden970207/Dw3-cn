-- Private, read-only capture of the inspected deck-selection submenu.
local ffi, bit = require('ffi'), require('bit')
local mem, frame, obj, since, done = ffi.cast('uint8_t *', PCSX.getMemPtr()), 0, nil, nil, false
local out = assert(os.getenv('PSXSTACK_REPLAY_OUT'))
local function u32(pos) return tonumber(ffi.cast('uint32_t *', mem + pos)[0]) end
local function capture()
    local pic = PCSX.GPU.takeScreenShot()
    assert(pic.data.size == pic.width * pic.height * 2, 'unsupported screenshot format')
    local data = ffi.cast('const uint16_t *', pic.data.data)
    local f = assert(io.open(out .. '/PRIVATE_deck_menu.ppm', 'wb'))
    f:write(string.format('P6\n%d %d\n255\n', pic.width, pic.height))
    for i = 0, pic.width * pic.height - 1 do
        local c = data[i]
        f:write(string.char(bit.band(c,31)*8, bit.band(bit.rshift(c,5),31)*8,
                            bit.band(bit.rshift(c,10),31)*8))
    end
    f:close(); print('deck_screenshot=menu frame=' .. frame)
end
card_deck_listener = PCSX.Events.createEventListener('GPU::Vsync', function()
    frame = frame + 1
    if u32(0x55d28) ~= 4 then obj, since = nil, nil; return end
    if not obj and frame % 30 == 0 then
        for p = 0xab800, 0x1fec00, 4 do
            if u32(p + 0x48) == 0x80089e98 then obj = p; break end
        end
    end
    if obj and u32(obj + 0x0c) == 1 and u32(obj + 0x10) == 13 then
        if not since then since = frame; print('deck_submenu_ready frame=' .. frame) end
        if not done and frame == since + 60 then done = true; capture() end
    else
        since = nil
    end
end)
dofile(os.getenv('DW3_REPLAY_RUN_LUA'))
