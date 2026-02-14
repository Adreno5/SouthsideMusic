import logging
import threading
import tornado.websocket
import tornado.httpserver
import tornado.ioloop
import tornado.web
from PySide6.QtCore import QObject, Signal, QTimer

class WebSocketHandler(tornado.websocket.WebSocketHandler):
    def __init__(self, application, request, **kwargs) -> None:
        self.count = 0
        ws_handler.onSend.connect(self.trySend)
        ws_handler.onGetHandler.connect(lambda: ws_handler.onHandlerReceived.emit(self))
        super().__init__(application, request, **kwargs)

    def trySend(self, msg: str):
        try:
            if ws_handler.is_open:
                self.write_message(msg)
        except:
            pass

    def open(self, *args: str, **kwargs: str):
        logging.info('java client connected')
        ws_handler.onConnected.emit()
    
    def on_message(self, message):
        ws_handler.onMessage.emit(message)

    def on_close(self):
        logging.info('java client disconnected')
        ws_handler.onDisconnected.emit()

class QObjectHandler(QObject):
    onConnected = Signal()
    onDisconnected = Signal()
    onMessage = Signal(str)
    onSend = Signal(str)

    onGetHandler = Signal()
    onHandlerReceived = Signal(WebSocketHandler)

    is_open: bool = False
    sent: int = 0

    def getHandler(self):
        self.onGetHandler.emit()

    def __init__(self) -> None:
        super().__init__()
        self.onConnected.connect(lambda: self.__setattr__('is_open', True))
        self.onDisconnected.connect(lambda: self.__setattr__('is_open', False))
    
    def send(self, msg: str):
        self.onSend.emit(msg)

class WebSocketServer(threading.Thread):
    def __init__(self, port=12513):
        super().__init__()
        self.port = port
        self.app = tornado.web.Application([(r'/', WebSocketHandler)])
        self.server: tornado.httpserver.HTTPServer | None = None
        self.ioloop = None
        self.handler: WebSocketHandler | None = None

        self.tryGetHandler()

    def tryGetHandler(self):
        ws_handler.onHandlerReceived.connect(lambda handler: self.__setattr__('handler', handler))
        ws_handler.getHandler()

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
        logging.debug(str(self.handler))
        if self.handler:
            self.handler.close()
        logging.info('closed websocket')

        ws_handler.is_open = False
        self.join()

ws_handler = QObjectHandler()
ws_server = WebSocketServer(port=15489)