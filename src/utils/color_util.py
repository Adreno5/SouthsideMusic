from functools import lru_cache

from PySide6.QtGui import QColor

class HashableQColor(QColor):
    def __hash__(self) -> int:
        return hash((self.red(), self.green(), self.blue(), self.alpha()))

def mixColor(a: QColor, b: QColor, ratio: float = 0.5) -> QColor:
    return _mixColor(HashableQColor(a), HashableQColor(b), ratio)

@lru_cache
def _mixColor(a: QColor, b: QColor, ratio: float = 0.5) -> QColor:
    return QColor(
        int(a.red() * ratio + b.red() * (1 - ratio)),
        int(a.green() * ratio + b.green() * (1 - ratio)),
        int(a.blue() * ratio + b.blue() * (1 - ratio)),
        int(a.alpha() * ratio + b.alpha() * (1 - ratio))
    )