from functools import lru_cache
from typing import Literal
from PySide6.QtGui import QIcon
import darkdetect

def getQIcon(name: str, theme: Literal['dark', 'light', 'auto']='auto'):
    return _getQIcon_impl(name, theme)

@lru_cache
def _getQIcon_impl(name: str, theme: Literal['dark', 'light', 'auto']='auto'):
    if theme == 'auto':
        return QIcon(f'icons/{name}_{'dark' if darkdetect.isDark() else 'light'}.svg')
    elif theme == 'dark':
        return QIcon(f'icons/{name}_dark.svg')
    else:
        return QIcon(f'icons/{name}_light.svg')