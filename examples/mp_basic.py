import multiprocessing
import time

import socklocks

mp = multiprocessing.get_context()
with socklocks.replace_mp_context_locks(mp):
    mp.Pool().map(time.sleep, [1] * 5)
