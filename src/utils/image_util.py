from functools import lru_cache

from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import Qt
import numpy as np
import logging

def getAverageColor(pixmap: QPixmap) -> list[float]:
    return _getAverageColor(pixmap)

@lru_cache
def _getAverageColor(pixmap: QPixmap) -> list[float]:
    if pixmap and not pixmap.isNull():
        image = pixmap.toImage().convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        image = image.scaled(
            pixmap.width(), pixmap.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        buf = memoryview(image.bits())[: image.sizeInBytes()]
        bpl = image.bytesPerLine()
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(
            pixmap.height(), bpl
        )[:, : pixmap.width() * 4].reshape(
            pixmap.height(), pixmap.width(), 4
        )
        avg_color = np.mean(arr[:, :, :3], axis=(0, 1))
    else:
        avg_color = np.array([128, 128, 128], dtype=np.uint8)
    avg_color = avg_color.tolist()
    logging.debug(f"{avg_color=}")
    return avg_color