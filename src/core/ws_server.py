import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from core.free_threaded_worker import FreeThreadedJsonSender, dumpJsonPayload
from tornado.websocket import WebSocketClosedError
import tornado.websocket
import tornado.httpserver
import tornado.ioloop
import tornado.web
from imports import QObject, Signal
from services.events import event_bus, COLLECT_DEBUG_INFO, EMIT_DEBUG_INFO


class WebSocketHandler(tornado.websocket.WebSocketHandler):
    def __init__(self, application, request, **kwargs) -> None:
        self._logger = logging.getLogger(__name__)
        self.count = 0
        self.ioloop: tornado.ioloop.IOLoop | None = None
        ws_handler.onSend.connect(self.trySend)
        super().__init__(application, request, **kwargs)

    def trySend(self, msg: str):
        try:
            if not ws_handler.isCurrentHandler(self) or self.ioloop is None:
                return
            self.ioloop.add_callback(lambda: self._write_message(msg))
        except Exception:
            pass

    def _write_message(self, msg: str) -> None:
        try:
            if ws_handler.isCurrentHandler(self) and self.ws_connection is not None:
                future = self.write_message(msg)
                future.add_done_callback(self._on_write_done)
        except WebSocketClosedError:
            pass
        except Exception:
            pass

    def _on_write_done(self, future) -> None:
        try:
            future.result()
        except WebSocketClosedError:
            pass
        except Exception as e:
            if ws_handler.isCurrentHandler(self):
                self._logger.debug('websocket write failed: %s', e)

    def open(self, *args: str, **kwargs: str):
        self.ioloop = tornado.ioloop.IOLoop.current()
        self._logger.info('java client connected')
        ws_handler.openHandler(self)

    def on_message(self, message):
        if ws_handler.isCurrentHandler(self):
            ws_handler.messaged(message)
            if ws_handler.handlePingMessage(message):  # type: ignore
                return
            ws_handler.onMessage.emit(message)

    def on_close(self):
        if ws_handler.closeHandler(self):
            self._logger.info('java client disconnected')
        else:
            self._logger.debug('ignored stale java websocket close')
        try:
            ws_handler.onSend.disconnect(self.trySend)
        except (RuntimeError, TypeError):
            pass


class QObjectHandler(QObject):
    onConnected = Signal()
    onDisconnected = Signal()
    onMessage = Signal(str)
    onSend = Signal(str)

    onGetHandler = Signal()
    onHandlerReceived = Signal(WebSocketHandler)

    is_open: bool = False
    sent: float = 0  # unit: mb
    received: float = 0  # unit: kb
    ping: float = 0.0  # unit: ms

    def __init__(self) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self._handlers: set[WebSocketHandler] = set()
        self._current_handler: WebSocketHandler | None = None
        self._send_lock = threading.Lock()
        self._json_lock = threading.Lock()
        self._json_executor = ThreadPoolExecutor(
            max_workers=3,
            thread_name_prefix='southside-ws-json',
        )
        self._ft_json_sender = FreeThreadedJsonSender(logger=self._logger)
        self._json_pending: dict[str, Future[None]] = {}
        self._json_queued: dict[str, Callable[[], dict[str, object]]] = {}
        self._send_generation = 0
        self._json_shutdown = False
        self._ping_id = 0
        self._ping_started_at = 0.0
        self._ping_waiting = False
        self._ping_timer: threading.Timer | None = None
        self.onMessage.connect(self.messaged)
        event_bus.subscribe(COLLECT_DEBUG_INFO, self.emitDebugInfo)

    def messaged(self, msg):
        self.received += len(msg) / 1024.0

    def getHandler(self) -> WebSocketHandler | None:
        if self._current_handler is not None:
            self.onHandlerReceived.emit(self._current_handler)
        return self._current_handler

    def isCurrentHandler(self, handler: WebSocketHandler) -> bool:
        return self.is_open and self._current_handler is handler

    def openHandler(self, handler: WebSocketHandler) -> None:
        was_open = self.is_open
        self._handlers.add(handler)
        self._current_handler = handler
        self.is_open = True
        self._send_generation += 1
        self.onHandlerReceived.emit(handler)
        if not was_open:
            self.sent = 0
            self.ping = 0.0
            self.received = 0
            self.onConnected.emit()
        self._schedulePingRound(0.1)

    def closeHandler(self, handler: WebSocketHandler) -> bool:
        self._handlers.discard(handler)
        if self._current_handler is handler:
            self._current_handler = next(iter(self._handlers), None)
            self._send_generation += 1
        if self._handlers:
            self.is_open = True
            return False
        if not self.is_open:
            return False
        self.is_open = False
        self._send_generation += 1
        self._cancelPingTimer()
        self._ping_waiting = False
        self.onDisconnected.emit()
        return True

    def clearHandlers(self) -> None:
        self._handlers.clear()
        self._current_handler = None
        self.is_open = False
        self._send_generation += 1
        self._cancelPingTimer()
        self._ping_waiting = False
        with self._json_lock:
            self._json_queued.clear()

    def emitDebugInfo(self):
        event_bus.emit(
            EMIT_DEBUG_INFO,
            'QObjectHandler',
            [
                f'is_open={self.is_open}',
                f'handlers={len(self._handlers)}',
                f'current_handler={self._current_handler is not None}',
                f'sent={self.sent}',
                f'received={self.received}',
                f'ping={self.ping:.2f}ms',
            ],
        )

    def handlePingMessage(self, message: str) -> bool:
        if not isinstance(message, str) or 'ws_pong' not in message:
            return False
        try:
            import json

            payload = json.loads(message)
        except Exception:
            return False
        if not isinstance(payload, dict) or payload.get('option') != 'ws_pong':
            return False

        ping_id = payload.get('id')
        if ping_id != self._ping_id or not self._ping_waiting:
            return True

        self.ping = max(0.0, (time.perf_counter() - self._ping_started_at) * 1000.0)
        self._ping_waiting = False
        self._sendPingPacket('ws_pong', 'python_pong', ping_id)
        self._schedulePingRound(1)
        return True

    def _schedulePingRound(self, delay: float) -> None:
        self._cancelPingTimer()
        generation = self._send_generation
        timer = threading.Timer(delay, lambda: self._startPingRound(generation))
        timer.daemon = True
        self._ping_timer = timer
        timer.start()

    def _cancelPingTimer(self) -> None:
        if self._ping_timer is not None:
            self._ping_timer.cancel()
            self._ping_timer = None

    def _startPingRound(self, generation: int) -> None:
        if (
            not self.is_open
            or generation != self._send_generation
            or self._ping_waiting
        ):
            return
        self._ping_id += 1
        self._ping_started_at = time.perf_counter()
        self._ping_waiting = True
        self._sendPingPacket('ws_ping', 'python_ping', self._ping_id)

    def _sendPingPacket(self, option: str, stage: str, ping_id: object) -> None:
        try:
            self.send(
                dumpJsonPayload(
                    {
                        'option': option,
                        'stage': stage,
                        'id': ping_id,
                        'sent_at': time.perf_counter(),
                    }
                )
            )
        except Exception as e:
            self._logger.debug('failed to send websocket ping packet: %s', e)

    def send(self, msg: str):
        if not self.is_open:
            return
        with self._send_lock:
            self.sent += len(msg) / 1048576.0  # 1024 * 1024
        self.onSend.emit(msg)

    def sendJson(
        self,
        payload: dict[str, object],
        coalesce_key: str | None = None,
    ) -> None:
        self.sendJsonFactory(lambda: payload, coalesce_key=coalesce_key)

    def sendJsonFactory(
        self,
        factory: Callable[[], dict[str, object]],
        coalesce_key: str | None = None,
    ) -> None:
        if not self.is_open:
            return
        if self._json_shutdown:
            return
        if coalesce_key is None:
            generation = self._send_generation
            try:
                self._json_executor.submit(self._sendJsonFactory, factory, generation)
            except RuntimeError:
                return
            return

        with self._json_lock:
            future = self._json_pending.get(coalesce_key)
            if future is not None and not future.done():
                self._json_queued[coalesce_key] = factory
                return
            generation = self._send_generation
            try:
                future = self._json_executor.submit(
                    self._sendJsonFactoryLoop, coalesce_key, factory, generation
                )
            except RuntimeError:
                return
            self._json_pending[coalesce_key] = future

    def _sendJsonFactoryLoop(
        self,
        coalesce_key: str,
        factory: Callable[[], dict[str, object]],
        generation: int,
    ) -> None:
        current_factory = factory
        while True:
            self._sendJsonFactory(current_factory, generation)
            with self._json_lock:
                next_factory = self._json_queued.pop(coalesce_key, None)
                if next_factory is None:
                    self._json_pending.pop(coalesce_key, None)
                    return
                current_factory = next_factory

    def _sendJsonFactory(
        self,
        factory: Callable[[], dict[str, object]],
        generation: int,
    ) -> None:
        if not self.is_open or generation != self._send_generation:
            return
        try:
            payload = factory()
        except Exception as e:
            self._logger.exception(e)
            return

        try:
            msg = self._ft_json_sender.dump(payload)
            if msg is None:
                msg = dumpJsonPayload(payload)
        except Exception as e:
            self._logger.exception(e)
            return
        if generation == self._send_generation:
            self.send(msg)

    def shutdownJsonSender(self) -> None:
        self._json_shutdown = True
        self._json_executor.shutdown(wait=False, cancel_futures=True)
        self._ft_json_sender.shutdown()


class WebSocketServer(threading.Thread):
    def __init__(self, port=12513):
        super().__init__(daemon=True)
        self._logger = logging.getLogger(__name__)
        self.port = port
        self.app = tornado.web.Application([(r'/', WebSocketHandler)])
        self.server: tornado.httpserver.HTTPServer | None = None
        self.ioloop = None
        self.handler: WebSocketHandler | None = None
        self._stopping = False

        ws_handler.onHandlerReceived.connect(self._setHandler)
        self.tryGetHandler()
        event_bus.subscribe(COLLECT_DEBUG_INFO, self.emitDebugInfo)

    def emitDebugInfo(self):
        event_bus.emit(
            EMIT_DEBUG_INFO,
            'WebSocketServer',
            [
                f'port={self.port}',
                f'alive={self.is_alive()}',
                f'handler={self.handler is not None}',
                f'ioloop={self.ioloop is not None}',
            ],
        )

    def _setHandler(self, handler: WebSocketHandler) -> None:
        self.handler = handler

    def tryGetHandler(self):
        self.handler = ws_handler.getHandler()

    def run(self):
        try:
            self.server = tornado.httpserver.HTTPServer(self.app)
            if not self.server:
                return
            self.server.listen(self.port)
            self.ioloop = tornado.ioloop.IOLoop.current()
            self._logger.info(f'webSocket server started on port {self.port}')
            if self._stopping:
                self.server.stop()
                return
            self.ioloop.start()
        finally:
            if self.server:
                self.server.stop()
            self.server = None
            self.handler = None
            if self.ioloop is not None:
                try:
                    self.ioloop.close(all_fds=True)
                except Exception:
                    pass
            self.ioloop = None

    def stop(
        self,
        shutdown_json_sender: bool = False,
        timeout: float = 2.0,
    ) -> None:
        self._stopping = True

        def _stop_on_ioloop() -> None:
            if self.server:
                self.server.stop()
            if self.handler:
                self.handler.close()
            if self.ioloop:
                self.ioloop.stop()

        if self.ioloop:
            try:
                self.ioloop.add_callback(_stop_on_ioloop)
            except RuntimeError:
                _stop_on_ioloop()
        elif self.server:
            self.server.stop()
        self._logger.debug(str(self.handler))
        self._logger.info('closed websocket')

        ws_handler.clearHandlers()
        if shutdown_json_sender:
            ws_handler.shutdownJsonSender()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=timeout)
            if self.is_alive():
                self._logger.warning('websocket thread did not stop within timeout')


ws_handler = QObjectHandler()
ws_server = WebSocketServer(port=15489)
