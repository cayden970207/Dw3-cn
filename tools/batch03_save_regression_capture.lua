-- Combine read-only shop observations with an independent save-write watcher.
dofile(assert(os.getenv('DW3_CAPTURE_BATCH03')))
local ffi, bit = require('ffi'), require('bit')
local mem = ffi.cast('uint8_t *', PCSX.getMemPtr())
local out = assert(os.getenv('PSXSTACK_REPLAY_OUT'))
local frame, seen_limit, seen_booster, started, finished = 0, false, false, nil, nil
local function u32(p) return tonumber(ffi.cast('uint32_t *', mem + p)[0]) end
local function save_screen()
    local pic = PCSX.GPU.takeScreenShot()
    assert(pic.data.size == pic.width * pic.height * 2, 'unsupported screenshot')
    local data = ffi.cast('const uint16_t *', pic.data.data)
    local f = assert(io.open(out .. '/PRIVATE_saved.ppm', 'wb'))
    f:write(string.format('P6\n%d %d\n255\n', pic.width, pic.height))
    for i = 0, pic.width * pic.height - 1 do
        local c = data[i]
        f:write(string.char(bit.band(c,31)*8, bit.band(bit.rshift(c,5),31)*8,
                            bit.band(bit.rshift(c,10),31)*8))
    end
    f:close()
end
batch03_save_listener = PCSX.Events.createEventListener('GPU::Vsync', function()
    frame = frame + 1
    local stage = u32(0x55d28)
    if stage == 19 and frame % 30 == 0 then
        for p = 0xab800, 0x1fec00, 4 do
            local fn = u32(p + 0x48)
            if fn == 0x80087a2c and u32(p + 0xc) == 1 and u32(p + 0x10) == 12 and u32(p + 0x14) == 20 then
                seen_limit = true
            elseif fn == 0x800852cc and u32(p + 0xc) == 1 and u32(p + 0x10) == 4 and u32(p + 0x3c0) > 0 then
                seen_booster = true
            end
        end
    elseif stage == 12 and seen_limit and seen_booster then
        if not started and u32(0x48750) == 4 then
            started = frame; print('batch03_save_write_started_frame=' .. frame)
        end
        if started and not finished and u32(0x48a50) == 0x2700 then
            finished = frame; print('batch03_save_write_finished_frame=' .. frame)
        end
        if finished and frame == finished + 100 then save_screen() end
    end
end)
dofile(assert(os.getenv('DW3_REPLAY_RUN_LUA')))
