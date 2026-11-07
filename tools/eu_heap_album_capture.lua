-- Sample the EU heap's linked free blocks without writing emulated RAM.
-- Addresses bind to the hash-checked EU executable used by the replay.
local ffi = require('ffi')
local mem = ffi.cast('uint8_t *', PCSX.getMemPtr())
local frame = 0
local function u32(p) return tonumber(ffi.cast('uint32_t *', mem + p)[0]) end
local function offset(ptr) return ptr - 0x80000000 end
eu_heap_listener = PCSX.Events.createEventListener('GPU::Vsync', function()
    frame = frame + 1
    if frame % 30 ~= 0 then return end
    local first, ending = u32(0x4b5e0), u32(0x4b5e4)
    if first ~= 0x800ab800 or ending ~= 0x801ff000 then return end
    local p, largest, total, freeblocks, blocks = first, 0, 0, 0, 0
    while p < ending and blocks < 10000 do
        local at = offset(p)
        local nextp, state = u32(at + 4), u32(at + 8)
        if state == 1 then
            if p + 12 ~= ending then
                print(string.format('eu_heap_invalid frame=%d reason=terminator', frame))
                return
            end
            print(string.format('eu_heap_sample frame=%d stage=%d largest=%d total=%d freeblocks=%d blocks=%d',
                                frame, u32(0x55d28), largest, total, freeblocks, blocks))
            return
        end
        if nextp <= p or nextp > ending - 12 then
            print(string.format('eu_heap_invalid frame=%d reason=next', frame))
            return
        end
        if state == 0 then
            local available = nextp - p - 12
            if available < 0 then
                print(string.format('eu_heap_invalid frame=%d reason=size', frame))
                return
            end
            total = total + available
            if available > largest then largest = available end
            freeblocks = freeblocks + 1
        end
        blocks = blocks + 1
        p = nextp
    end
    print(string.format('eu_heap_invalid frame=%d reason=loop', frame))
end)
dofile(assert(os.getenv('DW3_HEAP_NEXT_LUA')))
