#!/usr/bin/env python3
# Intended to be started around the same time as script_1.py
import time

import socklocks

print(f'Script 2 started.')
start = time.monotonic()

with socklocks.SocketLock('fancy_lock'):
    time.sleep(10)
    print(f'Script 2 finished in {time.monotonic() - start} seconds.')
