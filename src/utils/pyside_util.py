
from PySide6.QtWidgets import * # type: ignore
from PySide6.QtCore import * # type: ignore

def remove_widgets(layout: QLayout) -> None:
    if layout is None:
        return
        
    for w in layout.findChildren(QWidget):
        w.deleteLater()