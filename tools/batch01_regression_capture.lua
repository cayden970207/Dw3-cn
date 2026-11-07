-- Observe batch-01 album, deck, shop and a fresh save write; never write RAM.
-- Screen captures contain game imagery and must remain local.
local ffi, bit = require('ffi'), require('bit')
local mem = ffi.cast('uint8_t *', PCSX.getMemPtr())
local out = assert(os.getenv('PSXSTACK_REPLAY_OUT'))
local frame, stage, album_start = 0, nil, nil
local shop, shop_step, shop_since, deck, deck_since = nil, nil, nil, nil, nil
local captured, visited_shop, visited_deck = {}, false, false
local save_started, save_finished = nil, nil
local function u32(p) return tonumber(ffi.cast('uint32_t *', mem + p)[0]) end
local function screen(name)
    if captured[name] then return end
    local pic = PCSX.GPU.takeScreenShot()
    assert(pic.data.size == pic.width * pic.height * 2, 'unsupported screenshot')
    local data = ffi.cast('const uint16_t *', pic.data.data)
    local f = assert(io.open(out .. '/PRIVATE_' .. name .. '.ppm', 'wb'))
    f:write(string.format('P6\n%d %d\n255\n', pic.width, pic.height))
    for i = 0, pic.width * pic.height - 1 do
        local c = data[i]
        f:write(string.char(bit.band(c,31)*8, bit.band(bit.rshift(c,5),31)*8,
                            bit.band(bit.rshift(c,10),31)*8))
    end
    f:close(); captured[name] = true
    print('batch01_capture=' .. name .. ' frame=' .. frame)
end
batch01_listener = PCSX.Events.createEventListener('GPU::Vsync', function()
    frame = frame + 1
    local new_stage = u32(0x55d28)
    if new_stage ~= stage then
        stage, album_start = new_stage, nil
        shop, shop_step, shop_since, deck, deck_since = nil, nil, nil, nil, nil
    end
    if stage == 18 then
        if not album_start then album_start = frame end
        if frame == album_start + 360 then screen('album') end
    elseif stage == 4 then
        if not deck and frame % 30 == 0 then
            for p = 0xab800, 0x1fec00, 4 do
                if u32(p + 0x48) == 0x80089e98 then deck = p; break end
            end
        end
        if deck and u32(deck + 0xc) == 1 and u32(deck + 0x10) == 13 then
            if not deck_since then deck_since = frame; print('batch01_deck_submenu_frame=' .. frame) end
            visited_deck = true
            if frame == deck_since + 60 then screen('deck') end
        else deck_since = nil end
    elseif stage == 19 then
        if not shop and frame % 30 == 0 then
            for p = 0xab800, 0x1fec00, 4 do
                if u32(p + 0x48) == 0x80088b50 and u32(p + 0x60) == 49 then shop = p; break end
            end
        end
        if shop and u32(shop + 0xc) == 1 then
            local step, cursor = u32(shop + 0x10), u32(shop + 0x6c)
            if step ~= shop_step then
                shop_step, shop_since = step, frame
                print(string.format('batch01_shop_step=%d cursor=%d frame=%d', step, cursor, frame))
            end
            if step == 3 then visited_shop = true end
            if frame == shop_since + 60 then
                if step == 3 then screen('shop') elseif step == 22 and cursor == 1 then screen('empty') end
            end
        end
    elseif stage == 12 and visited_shop and visited_deck then
        if not save_started and u32(0x48750) == 4 then
            save_started = frame; print('batch01_save_write_started_frame=' .. frame)
        end
        if save_started and not save_finished and u32(0x48a50) == 0x2700 then
            save_finished = frame; print('batch01_save_write_finished_frame=' .. frame)
        end
        if save_finished and frame == save_finished + 100 then screen('saved') end
    end
end)
dofile(os.getenv('DW3_REPLAY_RUN_LUA'))
