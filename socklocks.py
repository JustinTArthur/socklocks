import array
import logging
import os.path
import platform
import random
import socket
import string
import sys
import tempfile
import time
from contextlib import AbstractContextManager, contextmanager
from struct import Struct
from typing import Optional, Union

import _thread

NATIVE_BYTE_ORDER = sys.byteorder
SUPPORTS_UNIX_SOCKS = hasattr(socket, 'AF_UNIX')
SUPPORTS_ABSTRACT_SOCKS = SUPPORTS_UNIX_SOCKS and platform.system() == 'Linux'
SUPPORTS_CMSG_SHARE = (
    SUPPORTS_UNIX_SOCKS
    and hasattr(socket.socket, 'sendmsg')
    and hasattr(socket, 'SCM_RIGHTS')
)
SUPPORTS_ANY_SHARE = hasattr(socket.socket, 'share')
DEFAULT_ALLOWED_PORTS = range(10000, 65536)
SIMPLE_CHAR_BYTES = (string.ascii_lowercase + string.digits).encode('ascii')

dword = Struct('L')
logger = logging.getLogger(__name__)


def hashable_to_port_number(hashable):
    hash(hashable)


class SocketLock(AbstractContextManager):
    PREFIX = b'sklk'

    def __init__(
        self,
        name: Optional[Union[bytes, bytearray, memoryview, str]] = None,
        max_waiters: int = 1024,
        allowed_inet_ports: Optional[Union[range, tuple, list]] = None
    ):
        if isinstance(name, str):
            # Use ASCII bytes for maximum compatibility with file systems.
            name = name.encode('ascii')

        self._needs_unlink = False
        if SUPPORTS_ABSTRACT_SOCKS and SUPPORTS_CMSG_SHARE:
            self._addr_family = socket.AF_UNIX
            if not name:
                namelen = 107 - len(self.PREFIX)
                name = bytes([random.getrandbits(8) for _ in range(namelen)])
            self._addr = b'\x00' + self.PREFIX + name
        elif SUPPORTS_UNIX_SOCKS and SUPPORTS_CMSG_SHARE:
            self._addr_family = socket.AF_UNIX
            if not name:
                name_len = random.randint(4, 22)
                name = bytes(random.choices(SIMPLE_CHAR_BYTES, k=name_len))
            self._addr = os.path.join(tempfile.gettempdirb(),
                                      self.PREFIX + name)
            self._needs_unlink = True
        elif SUPPORTS_ANY_SHARE:
            allowed_inet_ports = allowed_inet_ports or DEFAULT_ALLOWED_PORTS
            if name:
                position = hash(name) % (len(allowed_inet_ports) + 1)
                port = allowed_inet_ports[position]
            else:
                port = name = random.choice(allowed_inet_ports)
            self._addr_family = socket.AF_INET
            self._addr = ('127.0.0.1', port)
        else:
            raise NotImplementedError(
                'Socket handle sharing not implemented on this platform.'
            )
        self._name = name
        self._max_waiters = max_waiters
        self._socket = None

    if SUPPORTS_CMSG_SHARE:
        def _send_listening_fd(self, target_sock: socket.socket):
            msgs = (b'a',)
            cmsgs = (
                    (
                        socket.SOL_SOCKET,
                        socket.SCM_RIGHTS,
                        bytes(array.array("i", (self._socket.fileno(),)))
                    ),
            )
            target_sock.sendmsg(msgs, cmsgs)

        def _recv_listening_sock(self, source_sock):
            fds = array.array("i")  # Array of ints
            _msg, ancdata, flags, addr = source_sock.recvmsg(
                1,
                socket.CMSG_LEN(fds.itemsize)
            )
            for cmsg_level, cmsg_type, cmsg_data in ancdata:
                if (cmsg_level == socket.SOL_SOCKET and
                        cmsg_type == socket.SCM_RIGHTS):
                    # Append data, ignoring any truncated integers at the end.
                    cmsg_end = len(cmsg_data) - (len(cmsg_data) % fds.itemsize)
                    fds.fromstring(cmsg_data[:cmsg_end])
            if not _msg:
                logger.debug('Probable race condition. Waiting connection for '
                             '%s closed prematurely.', self._name)
                return False
            listening_fd = fds[0]
            self._socket = socket.fromfd(
                listening_fd,
                self._addr_family,
                socket.SOCK_STREAM
            )
            return True

    elif SUPPORTS_ANY_SHARE:
        def _send_listening_fd(self, target_sock: socket.socket):
            pid_buffer = bytearray()
            while len(pid_buffer) < dword.size:
                recvd = target_sock.recv(dword.size - len(pid_buffer))
                if not recvd:
                    return
                pid_buffer += recvd
            target_pid, = dword.unpack(pid_buffer)
            handle_bytes = self._socket.share(target_pid)
            target_sock.sendall(bytes(len(handle_bytes),) + handle_bytes)

        def _recv_listening_sock(self, source_sock):
            # Other end needs our PID to prepare a handle for us
            source_sock.sendall(dword.pack(os.getpid(),))
            handle_bytes = bytearray(source_sock.recv(8))
            if handle_bytes:
                expected_fd_bytes_length = handle_bytes[0] + 1
                while len(handle_bytes) < expected_fd_bytes_length:
                    recvd_bytes = source_sock.recv(8)
                    if not recvd_bytes:
                        # Socket closed during hand-over
                        handle_bytes = b''
                        break
                    handle_bytes += recvd_bytes

            if handle_bytes:
                self._socket = socket.fromshare(handle_bytes)
                return True
            return False

    def attempt_listen(self):
        self._socket = socket.socket(self._addr_family, socket.SOCK_STREAM)
        try:
            self._socket.bind(self._addr)
            self._socket.listen(self._max_waiters)
            return True
        except socket.error:
            # Someone else probably has the lock.
            return False

    def attempt_connect_and_recv(self):
        # Wait on someone else to give us the lock
        with socket.socket(self._addr_family, socket.SOCK_STREAM) as wait_sock:
            wait_sock.setblocking(True)
            connected = False
            for backoff in (0.0001, 0.001, 0.01, 0.1):
                try:
                    wait_sock.connect(self._addr)
                except ConnectionRefusedError:
                    logger.debug('Possible race condition. Connection for %s '
                                 'refused, possibly before listener could '
                                 'enter listen mode.', self._name)
                    time.sleep(backoff)
                except FileNotFoundError:
                    logger.debug('Possible race condition. Listener for %s '
                                 'closed and deleted file before we could '
                                 'connect.', self._name)
                    return False
                else:
                    connected = True
                    break

            if not connected:
                raise RuntimeError('Could not connect to current acquierer.')

            # Block until we receive the listening socket's handle or
            # are disconnected:
            acquired = self._recv_listening_sock(wait_sock)
        return acquired

    def acquire(self):
        while True:
            if self.attempt_listen():
                break
            if self.attempt_connect_and_recv():
                break

    def release(self):
        # Accept the first connect, it's the next-waiting acquirer we'll pass
        # the unlockededness to.
        self._socket.setblocking(False)
        try:
            next_acquirer = self._socket.accept()[0]
        except Exception:
            # Exceptions are OS dependent. Assume there were no waiting
            # acquirers.
            self._socket.close()
            if (self._needs_unlink
                    and isinstance(self._addr, (bytes, bytearray, str))):
                os.unlink(self._addr)
            return

        # Pass the listening socket fd to the next acquirer
        self._send_listening_fd(next_acquirer)

        # A clean disconnect means other side has duplicated the fd and we can
        # close ours.
        next_acquirer.setblocking(True)
        stuff = next_acquirer.recv(2)
        if stuff:
            logger.debug('Unexpected handoff response instead of FIN for %s: '
                         '%s', self._name, stuff)
        # Nothing else left to do with our own descriptors, close em
        next_acquirer.close()
        self._socket.close()

    def __enter__(self):
        self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class SocketLockThreadSafe(SocketLock):
    def __init__(self, *args, **kwargs):
        # Order of operations on the sockets is important and multithreaded
        # activity in a lock instance can mess that order up, so we use
        # a typical thread lock to keep that from happening.
        self._thread_lock = _thread.allocate_lock()
        super().__init__(*args, **kwargs)

    def acquire(self):
        self._thread_lock.acquire()
        super().acquire()

    def release(self):
        super().release()
        self._thread_lock.release()


@contextmanager
def replace_mp_context_locks(mp_context):
    original_lock_factory = mp_context.Lock
    mp_context.Lock = SocketLockThreadSafe
    try:
        yield mp_context
    finally:
        mp_context.Lock = original_lock_factory
