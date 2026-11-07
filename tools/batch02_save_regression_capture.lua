-- Combine the two read-only observers; start the replay engine exactly once.
dofile(assert(os.getenv('DW3_CAPTURE_BATCH01')))
dofile(assert(os.getenv('DW3_CAPTURE_BATCH02')))
dofile(assert(os.getenv('DW3_REPLAY_RUN_LUA')))
