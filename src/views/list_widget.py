from qfluentwidgets import ListWidget, SmoothScrollDelegate


class SListWidget(ListWidget):
    """ListWidget with animation-based smooth scrolling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scrollDelegate = SmoothScrollDelegate(self, useAni=True)
