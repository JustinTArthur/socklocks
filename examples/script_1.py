#!/usr/bin/env python3
# Intended to be started around the same time as script_2.py
import time

import socklocks

print(f'Script 1 started.')
start = time.monotonic()

with socklocks.SocketLock('fancy_lock'):
    time.sleep(10)
    print(f'Script 1 finished in {time.monotonic() - start} seconds.')
