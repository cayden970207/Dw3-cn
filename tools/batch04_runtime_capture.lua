-- Read-only observations of the real card purchase confirmation and choices.
local ffi, bit = require('ffi'), require('bit')
local mem = ffi.cast('uint8_t *', PCSX.getMemPtr())
local out = assert(os.getenv('PSXSTACK_REPLAY_OUT'))
local frame, stage, buy, last, since, cursor, cursor_since = 0, nil, nil, nil, nil, nil, nil
local shop, shop_since, editor, editor_since = nil, nil, nil, nil
local captured = {}
local function u32(pos) return tonumber(ffi.cast('uint32_t *', mem + pos)[0]) end
local function u8(pos) return tonumber(mem[pos]) end
local function screen(label)
    if captured[label] then return end
    local pic = PCSX.GPU.takeScreenShot()
    assert(pic.data.size == pic.width * pic.height * 2, 'unsupported screenshot')
    local data = ffi.cast('const uint16_t *', pic.data.data)
    local f = assert(io.open(out .. '/PRIVATE_' .. label .. '.ppm', 'wb'))
    f:write(string.format('P6\n%d %d\n255\n', pic.width, pic.height))
    for i = 0, pic.width * pic.height - 1 do
        local c = data[i]
        f:write(string.char(bit.band(c,31)*8, bit.band(bit.rshift(c,5),31)*8,
                            bit.band(bit.rshift(c,10),31)*8))
    end
    f:close(); captured[label] = true
    print('batch04_capture=' .. label .. ' frame=' .. frame)
end
batch04_listener = PCSX.Events.createEventListener('GPU::Vsync', function()
    frame = frame + 1
    local now = u32(0x55d28)
    if now ~= stage then
        stage, buy, last, since, cursor, cursor_since = now, nil, nil, nil, nil, nil, nil
        shop, shop_since, editor, editor_since = nil, nil, nil, nil
    end
    if stage ~= 19 and stage ~= 4 then return end
    if frame % 30 == 0 then
        for p = 0xab800, 0x1fec00, 4 do
            local fn = u32(p + 0x48)
            if stage == 19 and fn == 0x80087a2c then buy = p end
            if stage == 19 and fn == 0x80088b50 and u32(p + 0x60) == 49 then shop = p end
            if stage == 4 and fn == 0x80086574 then editor = p end
        end
    end
    if stage == 4 then
        if editor and u32(editor + 0xc) == 1 and u32(editor + 0x10) == 5 then
            if not editor_since then editor_since = frame; print('batch04_editor_ready_frame=' .. frame) end
            if frame == editor_since + 60 then screen('deck_editor') end
        else editor_since = nil end
        return
    end
    if shop and u32(shop + 0xc) == 1 and u32(shop + 0x10) == 3 then
        if not shop_since then shop_since = frame; print('batch04_shop_ready_frame=' .. frame) end
        if frame == shop_since + 60 then screen('shop_main') end
    else shop_since = nil end
    if not buy then return end
    local state, step, sub = u32(buy + 0xc), u32(buy + 0x10), u32(buy + 0x14)
    local key = string.format('%d:%d:%d', state, step, sub)
    if key ~= last then
        last, since = key, frame
        print(string.format('batch04_buy state=%d step=%d substep=%d frame=%d money=%d card59_count=%d chosen=%d price=%d column=%d',
            state, step, sub, frame, u32(0x48d34 + 0x6c), u8(0x48d34 + 0x3a2 + 59),
            u32(buy + 0x98), u32(buy + 0x9c), u32(buy + 0x5c)))
    end
    if state == 1 and step == 7 then
        local current = u32(buy + 0x94)
        if current ~= cursor then
            cursor, cursor_since = current, frame
            print(string.format('batch04_question_cursor=%d frame=%d', cursor, frame))
        end
        if frame == cursor_since + 60 then screen(cursor == 0 and 'buy_prompt' or 'buy_no_selected') end
    else cursor, cursor_since = nil, nil end
end)
if os.getenv('DW3_CAPTURE_COMBINED') ~= '1' then dofile(os.getenv('DW3_REPLAY_RUN_LUA')) end
