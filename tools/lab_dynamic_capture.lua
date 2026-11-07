-- Observe the lab chart's title and inserted name without changing game state.
-- The text buffers and screenshots are private and must remain under local/.
local ffi, bit = require('ffi'), require('bit')
local mem = ffi.cast('uint8_t *', PCSX.getMemPtr())
local out = assert(os.getenv('PSXSTACK_REPLAY_OUT'))
local frame, chart, ready, captured = 0, nil, nil, false
local function u32(p) return tonumber(ffi.cast('uint32_t *', mem + p)[0]) end
local function s16(p) return tonumber(ffi.cast('int16_t *', mem + p)[0]) end
local function addr(p, size)
    if p < 0x80000000 or p + (size or 1) > 0x80200000 then return nil end
    return p - 0x80000000
end
local function capture(title)
    local lines = {}
    for i = 0, 1 do
        local p, length = addr(u32(title + 0x5c + i * 12)), s16(title + 0x62 + i * 12)
        assert(p and length > 0 and length < 512 and p + length < 0x200000, 'unsafe text pointer')
        local f = assert(io.open(out .. '/PRIVATE_lab_line' .. i .. '.bin', 'wb'))
        f:write(ffi.string(mem + p, length), '\0'); f:close()
        lines[i + 1] = {length, s16(title + 0x66 + i * 12)}
    end
    local info = assert(io.open(out .. '/PRIVATE_lab_window.json', 'w'))
    info:write(string.format('{"frame":%d,"chart_state":%d,"chart_step":%d,"x":%d,"y":%d,"font_pointer":%d,"measured_width":%d,"palette":%d,"fixed":%d,"scaled":%d,"line0_length":%d,"line0_is_sjis":%d,"line1_length":%d,"line1_is_sjis":%d}',
        frame, u32(chart + 0xc), u32(chart + 0x10), s16(title + 0xb0), s16(title + 0xb2), u32(title + 0x50), s16(title + 0xbc), mem[title + 0xc0], mem[title + 0xc2], u32(title + 0xcc),
        lines[1][1], lines[1][2], lines[2][1], lines[2][2])); info:close()
    local pic = PCSX.GPU.takeScreenShot()
    assert(pic.data.size == pic.width * pic.height * 2, 'unsupported screenshot')
    local data = ffi.cast('const uint16_t *', pic.data.data)
    local f = assert(io.open(out .. '/PRIVATE_lab_chart.ppm', 'wb'))
    f:write(string.format('P6\n%d %d\n255\n', pic.width, pic.height))
    for i = 0, pic.width * pic.height - 1 do
        local c = data[i]
        f:write(string.char(bit.band(c,31)*8, bit.band(bit.rshift(c,5),31)*8,
                            bit.band(bit.rshift(c,10),31)*8))
    end
    f:close()
    print('lab_dynamic_capture_frame=' .. frame)
end
lab_dynamic_listener = PCSX.Events.createEventListener('GPU::Vsync', function()
    frame = frame + 1
    if u32(0x55d28) ~= 13 then chart, ready = nil, nil; return end
    if not chart and frame % 30 == 0 then
        for p = 0xab800, 0x1fec00, 4 do
            if u32(p + 0x48) == 0x800842f4 then chart = p; break end
        end
    end
    if chart and u32(chart + 0xc) == 1 and u32(chart + 0x10) >= 2 then
        if not ready then ready = frame end
        if not captured and frame == ready + 60 then
            local children = addr(u32(chart + 0x24), 4)
            local title = children and addr(u32(children), 0x174)
            if title and mem[title + 0xc1] ~= 0 then captured = true; capture(title) end
        end
    else ready = nil end
end)
dofile(os.getenv('DW3_REPLAY_RUN_LUA'))
