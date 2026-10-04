from PySide6.QtCore import QObject, Signal


class TranslationHandler(QObject):
    textChanged = Signal(str)

    def __init__(self):
        super().__init__()
        self.current_text = ''

    def setText(self, text):
        self.current_text = text
        self.textChanged.emit(text)
