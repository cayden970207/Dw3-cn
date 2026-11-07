-- Private read-only observations for the fixed c713 card-album replay.
-- Screenshots contain game artwork and must remain local.
local ffi, bit = require('ffi'), require('bit')
local mem = ffi.cast('uint8_t *', PCSX.getMemPtr())
local out = assert(os.getenv('PSXSTACK_REPLAY_OUT'))
local frame, album, last_page, changed_at, save_done_at = 0, nil, nil, nil, nil
local save_armed = false
local captured, saw_last = {}, false
local function u32(offset) return tonumber(ffi.cast('uint32_t *', mem + offset)[0]) end
local function screenshot(name)
    local pic = PCSX.GPU.takeScreenShot()
    if pic.data.size ~= pic.width * pic.height * 2 then
        print('regression_screenshot_unsupported=' .. name)
        return
    end
    local data = ffi.cast('const uint16_t *', pic.data.data)
    local f = assert(io.open(out .. '/PRIVATE_' .. name .. '.ppm', 'wb'))
    f:write(string.format('P6\n%d %d\n255\n', pic.width, pic.height))
    for i = 0, pic.width * pic.height - 1 do
        local c = data[i]
        f:write(string.char(bit.band(c, 31)*8, bit.band(bit.rshift(c, 5),31)*8,
                            bit.band(bit.rshift(c, 10),31)*8))
    end
    f:close()
    print('regression_screenshot=' .. name .. ' frame=' .. frame)
end
album_regression_listener = PCSX.Events.createEventListener('GPU::Vsync', function()
    frame = frame + 1
    local stage = u32(0x55d28)
    if stage ~= 18 then
        album, last_page, changed_at = nil, nil, nil
        if saw_last and stage == 12 and u32(0x48750) == 4 and not save_armed then
            save_armed = true
            print('regression_save_write_started_frame=' .. frame)
        end
        if save_armed and stage == 12 and u32(0x48a50) == 0x2700 and not save_done_at then
            save_done_at = frame
            print('regression_save_done_frame=' .. frame)
        end
        if save_done_at and frame == save_done_at + 100 and stage == 12 then
            screenshot('post_album_save')
        end
        return
    end
    if not album and frame % 30 == 0 then
        -- Scan only the game heap for the matching byte-verified overlay's
        -- album update function and expected page-count field; no writes.
        for p = 0xab800, 0x1fee00, 4 do
            if u32(p + 0x48) == 0x80085540 and u32(p + 0x64) == 27 and u32(p + 0x60) < 27 then
                album = p
                print(string.format('regression_album_object=0x%X frame=%d', 0x80000000 + p, frame))
                break
            end
        end
    end
    if album then
        local page = u32(album + 0x60)
        if page ~= last_page then
            last_page, changed_at = page, frame
            if page == 26 then saw_last = true end
            print(string.format('regression_page=%d frame=%d input_enabled=%d', page + 1, frame, u32(album + 0x68)))
        end
        if changed_at and frame == changed_at + 60 then
            local name = page == 0 and (saw_last and 'page_first_return' or 'page_first') or
                         (page == 1 and 'page_second' or (page == 26 and 'page_last' or nil))
            if name and not captured[name] then captured[name] = true; screenshot(name) end
        end
    end
end)
dofile(os.getenv('DW3_REPLAY_RUN_LUA'))
