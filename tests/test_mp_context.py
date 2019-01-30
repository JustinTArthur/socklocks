import multiprocessing

import socklocks


def _add_one(value):
    return value + 1


def test_normal_mp_context():
    """
    Establishes a baseline, makes sure normal mp context works in test harness.
    """
    mp = multiprocessing.get_context()
    results = mp.Pool().map(_add_one, (1, 2, 3, 4, 5))
    assert results == [2, 3, 4, 5, 6]


def test_socklock_mp_context():
    """
    Just makes sure there are no exceptions.
    """
    mp = multiprocessing.get_context()
    with socklocks.replace_mp_context_locks(mp):
        assert issubclass(mp.Lock, socklocks.SocketLockThreadSafe)
        results = mp.Pool().map(_add_one, (1, 2, 3, 4, 5))

    # Original lock should be restored:
    assert not issubclass(mp.Lock, socklocks.SocketLock)

    assert results == [2, 3, 4, 5, 6]
