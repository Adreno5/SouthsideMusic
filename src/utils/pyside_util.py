
from imports import * # type: ignore
from imports import * # type: ignore

def remove_widgets(layout: QLayout) -> None:
    if layout is None:
        return
        
    for w in layout.findChildren(QWidget):
        w.deleteLater()