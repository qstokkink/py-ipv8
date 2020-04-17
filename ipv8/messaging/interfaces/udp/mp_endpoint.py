import asyncio
import marshal

from multiprocessing import context, freeze_support, Process, queues
from queue import Empty

from .endpoint import UDPEndpoint
from ..endpoint import Endpoint, EndpointClosedException, EndpointListener


class FasterForkingPickler(context.reduction.ForkingPickler):

    @classmethod
    def dumps(cls, obj, protocol=None):
        return marshal.dumps(obj)

    @classmethod
    def loads(cls, s, *, fix_imports=True, encoding="ASCII", errors="strict"):
        return marshal.loads(s)


setattr(queues, '_ForkingPickler', FasterForkingPickler)


class DangerQueue(queues.Queue):

    def __init__(self, maxsize=0):
        super(DangerQueue, self).__init__(maxsize, ctx=context._default_context)

    def put(self, obj, block=True, timeout=None):
        with self._notempty:
            if self._thread is None:
                self._start_thread()
            self._buffer.append(obj)
            self._notempty.notify()

    def get(self, block=True, timeout=None):
        return marshal.loads(self._recv_bytes())

    def get_nowait(self):
        if not self._poll():
            raise Empty
        return marshal.loads(self._recv_bytes())


class PacketListener(EndpointListener):

    def __init__(self, endpoint, receive_queue):
        super(PacketListener, self).__init__(endpoint, True)
        self.receive_queue = receive_queue

    def on_packet(self, packet):
        self.receive_queue.put(packet)


async def _hook_listener(open_task, ep, receive_queue):
    await open_task()

    receive_queue.put(ep._port)

    listener = PacketListener(ep, receive_queue)
    ep.add_listener(listener)


async def _consume(ep, send_queue):
    while True:
        try:
            item = send_queue.get_nowait()
        except Empty:
            await asyncio.sleep(0.01)
            continue
        try:
            ep.send(*item)
        except:
            pass


def _create_sp_socket(ip, port, send_queue, receive_queue):
    loop = asyncio.get_event_loop_policy().new_event_loop()
    asyncio.set_event_loop(loop)

    ep = UDPEndpoint(ip=ip, port=port)
    loop.run_until_complete(asyncio.gather(_hook_listener(ep.open, ep, receive_queue), _consume(ep, send_queue)))


class MPUDPEndpoint(Endpoint):

    def __init__(self, port=0, ip="0.0.0.0"):
        super(MPUDPEndpoint, self).__init__()

        # Endpoint info
        self._port = port
        self._ip = ip
        self._running = False

        # Byte counters
        self.bytes_up = 0
        self.bytes_down = 0

        # Process
        self._process = None
        self._send_queue = DangerQueue()
        self._receive_queue = DangerQueue()

    def assert_open(self):
        if not self._running:
            raise EndpointClosedException(self)

    def is_open(self):
        return self._running

    def get_address(self):
        return self._ip, self._port

    async def open(self):
        freeze_support()
        self._process = Process(target=_create_sp_socket,
                                args=(self._ip, self._port, self._send_queue, self._receive_queue))
        self._process.start()
        self._port = self._receive_queue.get(block=True)

        loop = asyncio.get_event_loop()
        loop.create_task(self.consume())

        self._running = True

    async def consume(self):
        while self._running:
            try:
                item = self._receive_queue.get_nowait()
            except:
                await asyncio.sleep(0.01)
                continue
            self.notify_listeners(item)
            self.bytes_down += len(item[1])

    def send(self, socket_address, packet):
        self.bytes_up += len(packet)
        self._send_queue.put((socket_address, packet))

    def close(self):
        self._running = False
        self._process.kill()
