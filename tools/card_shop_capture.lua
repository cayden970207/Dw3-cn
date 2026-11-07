-- Private, read-only observations for the verified card-shop overlay.
local ffi, bit = require('ffi'), require('bit')
local mem = ffi.cast('uint8_t *', PCSX.getMemPtr())
local out = assert(os.getenv('PSXSTACK_REPLAY_OUT'))
local frame, shop, last_step, step_at = 0, nil, nil, nil
local captured = {}
local function u32(pos) return tonumber(ffi.cast('uint32_t *', mem + pos)[0]) end
local function capture(name)
    local pic = PCSX.GPU.takeScreenShot()
    if pic.data.size ~= pic.width * pic.height * 2 then print('shop_capture_unsupported=' .. name); return end
    local data = ffi.cast('const uint16_t *', pic.data.data)
    local f = assert(io.open(out .. '/PRIVATE_' .. name .. '.ppm', 'wb'))
    f:write(string.format('P6\n%d %d\n255\n', pic.width, pic.height))
    for i = 0, pic.width * pic.height - 1 do
        local c = data[i]
        f:write(string.char(bit.band(c,31)*8, bit.band(bit.rshift(c,5),31)*8,
                            bit.band(bit.rshift(c,10),31)*8))
    end
    f:close()
    print('shop_screenshot=' .. name .. ' frame=' .. frame)
end
card_shop_listener = PCSX.Events.createEventListener('GPU::Vsync', function()
    frame = frame + 1
    if u32(0x55d28) ~= 19 then shop, last_step, step_at = nil, nil, nil; return end
    if not shop and frame % 30 == 0 then
        for p = 0xab800, 0x1fec00, 4 do
            if u32(p + 0x48) == 0x80088b50 and u32(p + 0x60) == 49 then
                shop = p
                print(string.format('shop_object=0x%X frame=%d', 0x80000000 + p, frame))
                break
            end
        end
    end
    if shop and u32(shop + 0x0c) == 1 then
        local step, cursor = u32(shop + 0x10), u32(shop + 0x6c)
        if step ~= last_step then
            last_step, step_at = step, frame
            print(string.format('shop_step=%d cursor=%d frame=%d', step, cursor, frame))
        end
        if step_at and frame == step_at + 60 then
            local name = step == 3 and 'shop_main' or (step == 22 and 'shop_no_boosters' or nil)
            if name and not captured[name] then captured[name] = true; capture(name) end
        end
    end
end)
dofile(os.getenv('DW3_REPLAY_RUN_LUA'))
