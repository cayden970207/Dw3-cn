-- Read-only observations for two condition-fixture card-shop scenarios.
local ffi, bit = require('ffi'), require('bit')
local mem = ffi.cast('uint8_t *', PCSX.getMemPtr())
local out = assert(os.getenv('PSXSTACK_REPLAY_OUT'))
local frame, stage, booster, buy = 0, nil, nil, nil
local last, since, captured, shown, shown_at = {}, {}, {}, nil, nil
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
    print('batch03_capture=' .. label .. ' frame=' .. frame)
end
local function observe(kind, obj)
    if not obj then return end
    local state, step, substep = u32(obj + 0xc), u32(obj + 0x10), u32(obj + 0x14)
    local key = string.format('%d:%d:%d', state, step, substep)
    if last[kind] ~= key then
        last[kind], since[kind] = key, frame
        print(string.format('batch03_%s state=%d step=%d substep=%d frame=%d money=%d booster_count=%d card65_count=%d chosen=%d price=%d list_count=%d',
            kind, state, step, substep, frame, u32(0x48d34 + 0x6c), u8(0x48d34 + 0x7c + 91),
            u8(0x48d34 + 0x3a2 + 65), kind == 'buy' and u32(obj + 0x98) or u32(obj + 0x5c),
            kind == 'buy' and u32(obj + 0x9c) or 0, kind == 'booster' and u32(obj + 0x3c0) or 0))
    end
    if frame == since[kind] + 60 and state == 1 then
        if kind == 'booster' and step == 4 then screen('booster_selection')
        elseif kind == 'booster' and step == 13 then screen('booster_opened')
        elseif kind == 'buy' and step == 12 and substep == 20 then screen('nine_card_limit') end
    end
    if kind == 'booster' and state == 1 and step == 13 then
        local now_shown = u32(obj + 0x88)
        if now_shown ~= shown then
            shown, shown_at = now_shown, frame
            print(string.format('batch03_booster_card_shown=%d frame=%d', shown, frame))
        end
        if shown == 1 and frame == shown_at + 60 then screen('booster_next_card') end
    end
end
batch03_listener = PCSX.Events.createEventListener('GPU::Vsync', function()
    frame = frame + 1
    local now = u32(0x55d28)
    if now ~= stage then stage, booster, buy, last, since, shown, shown_at = now, nil, nil, {}, {}, nil, nil end
    if stage == 19 and frame % 30 == 0 then
        for p = 0xab800, 0x1fec00, 4 do
            local fn = u32(p + 0x48)
            if fn == 0x800852cc then booster = p end
            if fn == 0x80087a2c then buy = p end
        end
    end
    if stage == 19 then observe('booster', booster); observe('buy', buy) end
end)
if os.getenv('DW3_CAPTURE_COMBINED') ~= '1' then dofile(os.getenv('DW3_REPLAY_RUN_LUA')) end
