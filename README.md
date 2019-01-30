# socklocks
This is a proof of concept of inter-process synchronization using sockets to
coordinate the pausing and resuming of code across multiple processes.

It's implemented in Python 3 using only the standard libraries. The code can
serve as reference for a faster implementation such as one written in C or Rust.

It's written by Justin Turner Arthur and is licensed under the Apache License
2.0.

## Usage
The primary lock classes are `SocketLock` and `SocketLockThreadSafe`. Like most
locks in Python, instances can be used as context managers using the `with`
statement.

```python
from socklocks import SocketLock


lock = SocketLock()
with lock:
    print('This code will run once lock is acquired.')
    print('It will release the lock afterwards')
```

The locks are purpose built for use in multiprocessing. They can be initialized
before a process is forked then acquired and released from the sub-processes.
```python
file_lock = SocketLock()
def hard_maths(increment):
    # Only one invocation should read/write from the file at a time
    with file_lock:
        with open('number.txt', 'r') as f:
            number = int(f.read())
        number = math.factorial(number) + increment
        with open('number.txt', 'w') as f:
            f.write(str(number))

multiprocessing.Pool().map(hard_maths, range(5))
```

They don't require forking. Multiple scripts could be run
independently that initialize the same effective lock by supplying the same
name.
```python
# script1.py
with SocketLock('critical_resource1'):
    do_stuff_to_res1()
```
```python
# script2.py
with SocketLock('critical_resource1'):
    do_other_stuff_to_res1()
```

If multiple threads within a process will need to acquire the same lock, use
the thread-safe `SocketLockThreadSafe`.

### Using it to work around AWS Lambda's missing SHM bug
AWS Lambda execution environments have an operating system that requires a SHM
filesystem mount (RAM disk), but such a filesystem is never mounted. This bug
doesn't usually show itself until you need to do something that would use this
mount, like use POSIX semaphores for inter-process synchronization.

CPython's multiprocessing and concurrent.futures modules use POSIX semaphores in
this way and when the OS tries to use SHM files to power POSIX sempahores it
fails:
```python-traceback
Traceback (most recent call last):
  File "/var/task/lambda_function.py", line 15, in process_things
    with ProcessPoolExecutor() as executor:
  File "/var/lang/lib/python3.6/concurrent/futures/process.py", line 390, in __init__
    EXTRA_QUEUED_CALLS)
  File "/var/lang/lib/python3.6/multiprocessing/context.py", line 102, in Queue
    return Queue(maxsize, ctx=self.get_context())
  File "/var/lang/lib/python3.6/multiprocessing/queues.py", line 42, in __init__
    self._rlock = ctx.Lock()
  File "/var/lang/lib/python3.6/multiprocessing/context.py", line 67, in Lock
    return Lock(ctx=self.get_context())
  File "/var/lang/lib/python3.6/multiprocessing/synchronize.py", line 163, in __init__
    SemLock.__init__(self, SEMAPHORE, 1, 1, ctx=ctx)
  File "/var/lang/lib/python3.6/multiprocessing/synchronize.py", line 60, in __init__
    unlink_now)
OSError: [Errno 38] Function not implemented
```

To get around this, you'd theoretically replace lock factories in your
multiprocessing context with corresponding socklocks constructors:
```python
import socklocks

# Raw multiprocessing:
with socklocks.replace_mp_context_locks(mp):
    mp.Pool.map(do_work, work_items)

# concurrent.futures in Python 3.7+:
with socklocks.replace_mp_context_locks(mp):
    with concurrent.futures.ProcessPoolExecutor(mp_context=mp) as executor:
        executor.map(do_work, work_items)
```
…however, the multiprocessing queues and pools also use
`multiprocessing.BoundedSemaphore`, which this library doesn't provide a
replacement for yet, so pools will not work—only basic locking will.

### Tests
In a local clone of the repo in a Python 3 env with socklocks installed:

        pip install pytest
        pytest

## How it works
Any attempt to acquire a lock starts with trying to bind a listening socket
to an address determined by the lock's name/id. If some other candidate has
already bound to that address, we assume they have the lock and connect to the
current acquirer's listening socket. If it's our turn to acquire the lock, the
current acquirer passes a socket handle of the listening socket to us. Once
we're done with the lock, we pass a handle of the listening socket to the next
connection waiting or close the listening socket if no one else is waiting.

Based on what's available in the Python implementation and operating system, the
following address types are used, in order of highest to lowest performance:
* Linux Abstract Socket Name
* Unix Domain Socket Path
* IPv4 address 127.0.0.1 on a determined IP port

Socket handles or file descriptors are passed using
[sendmsg](http://pubs.opengroup.org/onlinepubs/9699919799/functions/sendmsg.html)
in POSIX-compliant systems that support the `SCM_RIGHTS` control message type.
Otherwise, acquirers pass the listener their process ID and a handle is prepared
for the new acquirer using other means like
[Winsock shared sockets](https://docs.microsoft.com/en-us/windows/desktop/winsock/shared-sockets-2).

### Race Conditions that Result in Retried Operations
* A lock-holder might see no incoming connections and start shutting down the
listening socket only to have a new requester connect before the listening
socket is closed.
* An attempt to connect to a unix socket path might happen in between a
listening socket shutdown and the deletion of the file.
* A new requester might try to connect to the current acquirer's listening
socket before the socket has been put in listening mode, resulting in
connection refusal.

When Linux abstract sockets are used, many race conditions are mitigated
because there is no file to clean up. 

### Known Issues
* Currently only bytes or ASCII-compatible strings can be used as lock names.
* Windows socket descriptor sharing is untested. Let me know how it goes.
* When IP networking is the only infrastructure available, there is a higher
chance of lock names colliding because the system's port range is used
as a name space.
* Only basic locks are implemented. Re-entrant locks and semaphores are not
(yet?) part of this library.

## Comparison to other lock Mechanisms
### threading.Lock and _thread locks
The locks found in the Python standard library's `threading` and `_thread`
modules will generally perform better than the socket lock for synchronizing
code that only has multiple threads running from the same process. The point of
using sockets is to take advantage of the fact that they can be used for
inter-process synchronization.

### multiprocessing.Lock
`SocketLockThreadSafe` can be used as a drop-in replacement for
`multiprocessing.Lock`. This is useful if there are issues using the
`multiprocessing.Lock` supplied by your Python platform. The non-threadsafe
`SocketLock` will provide better performance where multi-threaded lock
acquisition isn't going to happen. If unsure, go with the thread-safe option.
