import threading

from imports import PRE_THEME_CHANGED, REFRESH_RATE_CHANGED, REPAINT, QApplication, QObject, QTimer, event_bus
from utils import darkdetect_util

class EventsServices(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app

        self.refresh_rate = max(60, app.primaryScreen().refreshRate() / 2)
        self.repaint_timer = QTimer(self)
        self.repaint_timer.timeout.connect(lambda: event_bus.emit(REPAINT))
        self.repaint_timer.start(int(1000 / self.refresh_rate))
        app.primaryScreen().refreshRateChanged.connect(lambda: event_bus.emit(REFRESH_RATE_CHANGED))
        event_bus.subscribe(REFRESH_RATE_CHANGED, self._onRefreshRateChanged)

        def _startListen():
            darkdetect_util.getDarkdetect().listener(lambda theme: event_bus.emit(PRE_THEME_CHANGED, theme))
        threading.Thread(target=_startListen, daemon=True).start()

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)

        self.repaint_timer.setInterval(int(1000 / self.refresh_rate))