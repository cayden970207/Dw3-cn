-- Record batch-02 source/translation windows and their real object states.
-- Screenshot files are private; the listener reads game RAM but never writes it.
local ffi, bit = require('ffi'), require('bit')
local mem = ffi.cast('uint8_t *', PCSX.getMemPtr())
local out = assert(os.getenv('PSXSTACK_REPLAY_OUT'))
local frame, stage, deck, name_entry, buy = 0, nil, nil, nil, nil
local last, since, captured = {}, {}, {}
local function u32(pos) return tonumber(ffi.cast('uint32_t *', mem + pos)[0]) end
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
    print('batch02_capture=' .. label .. ' frame=' .. frame)
end
local function observe(kind, obj)
    if not obj then return end
    local state, step, substep = u32(obj + 0xc), u32(obj + 0x10), u32(obj + 0x14)
    local key = string.format('%d:%d:%d', state, step, substep)
    if last[kind] ~= key then
        last[kind], since[kind] = key, frame
        print(string.format('batch02_%s state=%d step=%d substep=%d frame=%d', kind, state, step, substep, frame))
    end
    if frame == since[kind] + 60 and state == 1 then
        if kind == 'deck' and step == 4 then screen('deck_selection')
        elseif kind == 'deck' and step == 13 then screen('deck_menu')
        elseif kind == 'name' and step == 2 then screen('name_entry')
        elseif kind == 'buy' and step == 12 and substep == 19 then screen('insufficient_funds') end
    end
end
batch02_listener = PCSX.Events.createEventListener('GPU::Vsync', function()
    frame = frame + 1
    local now = u32(0x55d28)
    if now ~= stage then stage, deck, name_entry, buy, last, since = now, nil, nil, nil, {}, {} end
    if (stage == 4 or stage == 19) and frame % 30 == 0 then
        for p = 0xab800, 0x1fec00, 4 do
            local fn = u32(p + 0x48)
            if stage == 4 and fn == 0x80089e98 then deck = p end
            if stage == 4 and fn == 0x800884e8 then name_entry = p end
            if stage == 19 and fn == 0x80087a2c then buy = p end
        end
    end
    if stage == 4 then observe('deck', deck); observe('name', name_entry)
    elseif stage == 19 then observe('buy', buy) end
end)
dofile(os.getenv('DW3_REPLAY_RUN_LUA'))
