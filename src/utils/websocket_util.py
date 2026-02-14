import logging
import threading
import tornado.websocket
import tornado.httpserver
import tornado.ioloop
import tornado.web
from PySide6.QtCore import QObject, Signal, QTimer

class QObjectHandler(QObject):
    onConnected = Signal()
    onDisconnected = Signal()
    onMessage = Signal(str)
    onSend = Signal(str)
    
    def send(self, msg: str):
        self.onSend.emit(msg)

class WebSocketHandler(tornado.websocket.WebSocketHandler):
    def __init__(self, application, request, **kwargs) -> None:
        self.count = 0
        ws_handler.onSend.connect(self.write_message)
        super().__init__(application, request, **kwargs)

    def open(self, *args: str, **kwargs: str):
        logging.info('java client connected')
        ws_handler.onConnected.emit()
    
    def on_message(self, message):
        ws_handler.onMessage.emit(message)

    def on_close(self):
        logging.info('java client disconnected')
        ws_handler.onDisconnected.emit()

class WebSocketServer(threading.Thread):
    def __init__(self, port=12513):
        super().__init__()
        self.port = port
        self.app = tornado.web.Application([(r'/', WebSocketHandler)])
        self.server: tornado.httpserver.HTTPServer | None = None
        self.ioloop = None
        self.handler: WebSocketHandler

    def run(self):
        self.server = tornado.httpserver.HTTPServer(self.app)
        if not self.server:
            return
        self.server.listen(self.port)
        self.ioloop = tornado.ioloop.IOLoop.current()
        logging.info(f'webSocket server started on port {self.port}')
        self.ioloop.start()

    def stop(self):
        if self.ioloop:
            self.ioloop.add_callback(self.ioloop.stop)
        if self.server:
            self.server.stop()
        logging.info('closed websocket')

ws_handler = QObjectHandler()
ws_server = WebSocketServer(port=12513)