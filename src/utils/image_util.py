from functools import lru_cache

from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import Qt
import numpy as np
import logging


def getAverageColor(pixmap: QPixmap) -> list[float]:
    return _getAverageColor(tuple([pixmap.cacheKey()]), pixmap)


@lru_cache
def _getAverageColor(key: tuple, pixmap: QPixmap) -> list[float]:
    if pixmap and not pixmap.isNull():
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        return _avg_color_from_qimage(image)
    return [128, 128, 128]


def getAverageColorFromBytes(image_bytes: bytes) -> list[float]:
    qimg = QImage()
    qimg.loadFromData(image_bytes)
    if qimg.isNull():
        return [128, 128, 128]
    return _avg_color_from_qimage(qimg)


def _avg_color_from_qimage(image: QImage) -> list[float]:
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    buf = memoryview(image.bits())[: image.sizeInBytes()]
    bpl = image.bytesPerLine()
    h, w = image.height(), image.width()
    arr = (
        np.frombuffer(buf, dtype=np.uint8).reshape(h, bpl)[:, : w * 4].reshape(h, w, 4)
    )
    avg_color = np.mean(arr[:, :, :3], axis=(0, 1)).tolist()
    return avg_color
