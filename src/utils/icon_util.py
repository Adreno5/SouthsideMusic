import logging
from enum import Enum
from typing import Any, Literal, cast

import darkdetect
from imports import QIcon
from qfluentwidgets import FluentIconBase, Theme, isDarkTheme
import tempfile


class SouthsideIcon(FluentIconBase, Enum):
    FAV = "fav"
    EXPORT = "export"
    REMOVE = "remove"
    LAST = "last"
    NEXT = "next"
    PLAYA = "playa"
    PAUSE = "pause"
    PL_EXPAND = "pl_expand"
    PL_COLLAPSE = "pl_collapse"
    CLEARALL = "clearall"
    DISC = "disc"
    CNNT = "cnnt"
    PL = "pl"
    LOGIN = "login"
    MUSIC = "music"
    STUDIO = "studio"
    ISLAND = "island"
    SESSION = "session"
    DROP_UP = "drop_up"
    DROP_DOWN = "drop_down"

    def path(self, theme=Theme.AUTO) -> str:
        with open(f'icons/{self.value}.svg', 'r', encoding='utf-8') as f:
            svg = f.read()
        target = '#ffffff' if darkdetect.isDark() else '#000000'
        if theme == Theme.DARK:
            target = '#ffffff'
        elif theme == Theme.LIGHT:
            target = '#000000'
        if target != '#000000':
            svg = svg.replace('#000000', target)
        tmp = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.svg', delete=False)
        tmp.write(svg)
        tmp.close()
        return tmp.name

_icon_map = {icon.value: icon for icon in SouthsideIcon}


def getQIcon(name: str, theme: Literal["dark", "light", "auto"] = "auto"):
    icon = getFluentIcon(name)
    if theme == "auto":
        return icon.qicon()
    return icon.icon(Theme.DARK if theme == "dark" else Theme.LIGHT)


def getFluentIcon(name: str) -> SouthsideIcon:
    return _icon_map[name]


def bindIcon(
    widget: object, name: str, theme: Literal["dark", "light", "auto"] = "auto"
) -> None:
    if not hasattr(widget, "setIcon"):
        return
    if theme == "auto":
        cast(Any, widget).setIcon(getFluentIcon(name))
    else:
        cast(Any, widget).setIcon(getQIcon(name, theme))


def refreshBoundIcons() -> None:
    pass
