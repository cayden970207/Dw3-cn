-- Local-only screenshots for tools/replay_card_album.py.  The image data is
-- game artwork and must remain in the ignored replay directory.
local ffi, bit = require('ffi'), require('bit')
local frame, album_start, done = 0, nil, false
local out = os.getenv('PSXSTACK_REPLAY_OUT')
local mem = ffi.cast('uint8_t *', PCSX.getMemPtr())
local function stage() return tonumber(ffi.cast('uint32_t *', mem + 0x55d28)[0]) end
local function capture()
    local pic = PCSX.GPU.takeScreenShot()
    assert(pic.data.size == pic.width * pic.height * 2, 'unsupported screenshot format')
    local data = ffi.cast('const uint16_t *', pic.data.data)
    local file = assert(io.open(out .. '/PRIVATE_card_album.ppm', 'wb'))
    file:write(string.format('P6\n%d %d\n255\n', pic.width, pic.height))
    for i = 0, pic.width * pic.height - 1 do
        local c = data[i]
        file:write(string.char(bit.band(c, 31) * 8,
            bit.band(bit.rshift(c, 5), 31) * 8,
            bit.band(bit.rshift(c, 10), 31) * 8))
    end
    file:close()
    print('private_card_album_screenshot_frame=' .. frame)
end
card_album_listener = PCSX.Events.createEventListener('GPU::Vsync', function()
    frame = frame + 1
    if stage() == 18 and not album_start then album_start = frame end
    if album_start and not done and frame == album_start + 360 then
        capture()
        done = true
    end
end)
dofile(os.getenv('DW3_REPLAY_RUN_LUA'))
