from enum import Enum
from functools import lru_cache
from os import makedirs
from typing import Any, Literal, cast, override

from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer
from qfluentwidgets import FluentIconBase, Theme
from qfluentwidgets.common.icon import writeSvg

from core import theme as themeModule

makedirs('data', exist_ok=True)
makedirs('data/icons', exist_ok=True)


@lru_cache(maxsize=256)
def getSvgRenderer(
    path: str,
    indexes: tuple[int, ...] | None = None,
    attributes: tuple[tuple[str, str], ...] = (),
) -> QSvgRenderer:
    if attributes:
        return QSvgRenderer(writeSvg(path, indexes, **dict(attributes)).encode())
    return QSvgRenderer(path)


class CachedFluentIcon(FluentIconBase):
    def __init__(self, icon: FluentIconBase) -> None:
        self._icon = icon

    @override
    def path(self, theme: Theme = Theme.AUTO) -> str:
        return self._icon.path(theme)

    @override
    def render(
        self,
        painter: QPainter,
        rect: QRect | QRectF,
        theme: Theme = Theme.AUTO,
        indexes: list[int] | None = None,
        **attributes: str,
    ) -> None:
        actual_theme = Theme.DARK if themeModule.isDark() else Theme.LIGHT
        path = self.path(actual_theme if theme == Theme.AUTO else theme)
        getSvgRenderer(
            path,
            tuple(indexes) if indexes is not None else None,
            tuple(sorted(attributes.items())),
        ).render(painter, QRectF(rect))


class SouthsideIcon(FluentIconBase, Enum):
    ADD = 'add'
    FAV = 'fav'
    EXPORT = 'export'
    REMOVE = 'remove'
    LAST = 'last'
    NEXT = 'next'
    PLAYA = 'playa'
    PAUSE = 'pause'
    PL_EXPAND = 'pl_expand'
    PL_COLLAPSE = 'pl_collapse'
    CLEARALL = 'clearall'
    DISC = 'disc'
    CNNT = 'cnnt'
    PL = 'pl'
    LOGIN = 'login'
    MUSIC = 'music'
    STUDIO = 'studio'
    ISLAND = 'island'
    DROP_UP = 'drop_up'
    DROP_DOWN = 'drop_down'
    PLAYLIST = 'playlist'
    PLAYLIST_MULTIPLE_SELECTION = 'playlist_multiple_selection'
    SETTINGS = 'settings'
    SEARCH = 'search'
    RENAME = 'rename'
    TRANSLATION = 'translation'
    CHAT_ADD = 'chat_add'
    STOP_GEN = 'stop_gen'
    EDIT = 'edit'
    TRASH = 'trash'
    LIBRARY = 'library'
    COMMENT = 'comment'
    QUALITY = 'quality'

    @override
    def path(self, theme: Theme = Theme.AUTO) -> str:
        if theme == Theme.AUTO:
            theme = Theme.DARK if themeModule.isDark() else Theme.LIGHT
        return self._path(theme)

    @lru_cache(maxsize=128)
    def _path(self, theme: Theme) -> str:
        with open(f'icons/{self.value}.svg', 'r', encoding='utf-8') as f:
            svg = f.read()
        target = '#ffffff' if themeModule.isDark() else '#000000'
        if theme == Theme.DARK:
            target = '#ffffff'
        elif theme == Theme.LIGHT:
            target = '#000000'
        if target != '#000000':
            svg = svg.replace('#000000', target)

        save_path = f'data/icons/{self.value}_{theme.name}.svg'
        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(svg)
        return save_path

    @override
    def render(
        self,
        painter: QPainter,
        rect: QRect | QRectF,
        theme: Theme = Theme.AUTO,
        indexes: list[int] | None = None,
        **attributes: str,
    ) -> None:
        getSvgRenderer(
            self.path(theme),
            tuple(indexes) if indexes is not None else None,
            tuple(sorted(attributes.items())),
        ).render(painter, QRectF(rect))


_icon_map = {icon.value: icon for icon in SouthsideIcon}


def getQIcon(name: str, theme: Literal['dark', 'light', 'auto'] = 'auto'):
    icon = getFluentIcon(name)
    if theme == 'auto':
        return icon.qicon()
    return icon.icon(Theme.DARK if theme == 'dark' else Theme.LIGHT)


def getFluentIcon(name: str) -> SouthsideIcon:
    return _icon_map[name]


def bindIcon(
    widget: object, name: str, theme: Literal['dark', 'light', 'auto'] = 'auto'
) -> None:
    if not hasattr(widget, 'setIcon'):
        return
    if theme == 'auto':
        cast(Any, widget).setIcon(getFluentIcon(name))
    else:
        cast(Any, widget).setIcon(getQIcon(name, theme))


def refreshBoundIcons() -> None:
    pass
