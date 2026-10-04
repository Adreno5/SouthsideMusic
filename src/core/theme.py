import logging
import winreg

from darkdetect import isDark as isDarkDarkdetect
import darkdetect
from qfluentwidgets import ThemeColor, qconfig, setThemeColor

from PySide6.QtGui import QColor

_logger = logging.getLogger(__name__)
_is_dark = isDarkDarkdetect()


def _primaryColor() -> QColor:
    return QColor(qconfig.get(qconfig.themeColor))


setattr(ThemeColor.PRIMARY, 'color', _primaryColor)


def syncSystemThemeColor() -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\DWM'
        ) as key:
            value, _ = winreg.QueryValueEx(key, 'AccentColor')
    except OSError as e:
        _logger.exception(e)
        return

    color = QColor(value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)
    if color != qconfig.get(qconfig.themeColor):
        setThemeColor(color)


def getSystemThemeColor() -> QColor:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\DWM'
        ) as key:
            value, _ = winreg.QueryValueEx(key, 'AccentColor')
    except OSError as e:
        _logger.exception(e)
        return

    color = QColor(value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)
    return color


def isDark() -> bool:
    return bool(_is_dark)


def isLight() -> bool:
    return not bool(_is_dark)


def getDarkdetect():
    return darkdetect
