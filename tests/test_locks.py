import multiprocessing
import time

import socklocks


# Work functions sent to multiprocessing pools must be declared at the module
# level so that their reference can be pickled.
def _do_work(work, let_me_sleep_in_peace):
    # Only one worker should be sleeping at a time.
    with let_me_sleep_in_peace:
        time.sleep(0.1)
    return work + 1


def test_lock_across_processes():
    let_me_sleep_in_peace = socklocks.SocketLock()

    mp = multiprocessing.get_context()
    with socklocks.replace_mp_context_locks(mp):
        work = zip((1, 2, 3, 4, 5), [let_me_sleep_in_peace] * 5)

        start = time.monotonic()
        results = mp.Pool().starmap(_do_work, work)
        end = time.monotonic()

        assert end - start >= 0.5
        assert results == [2, 3, 4, 5, 6]
