from PySide6.QtWidgets import * # type: ignore
from PySide6.QtCore import * # type: ignore
from PySide6.QtGui import * # type: ignore
from qfluentwidgets import * # type: ignore

class LineinputDialog(MessageBoxBase):
    def __init__(self, parent, title: str, desc: str, place: str):
        super().__init__(parent)

        self.title_label = SubtitleLabel(title)
        self.desc_label = QLabel(desc)

        self.inputer = LineEdit(self)
        self.inputer.returnPressed.connect(self.accept)
        self.inputer.setPlaceholderText(place)
        self.inputer.setFixedWidth(parent.width() * 0.7)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.desc_label)
        self.viewLayout.addWidget(self.inputer)

        self.cancelButton.hide()

def get_text_lineedit(title: str, desc: str, place: str, parent: QWidget):
    dialog = LineinputDialog(parent, title, desc, place)
    dialog.exec()

    return dialog.inputer.text()

class TexteditDialog(MessageBoxBase):
    def __init__(self, parent, title: str, desc: str, place: str):
        super().__init__(parent)

        self.title_label = SubtitleLabel(title)
        self.desc_label = QLabel(desc)

        self.inputer = TextEdit(self)
        self.inputer.setPlaceholderText(place)
        self.inputer.setFixedSize(parent.size() * 0.65)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.desc_label)
        self.viewLayout.addWidget(self.inputer)

        self.cancelButton.hide()

def get_text_textedit(title: str, desc: str, place: str, parent: QWidget):
    dialog = TexteditDialog(parent, title, desc, place)
    dialog.exec()

    return dialog.inputer.toPlainText()

class ListDialog(MessageBoxBase):
    def __init__(self, parent, title: str, desc: str, texts: list[str], selection: QListWidget.SelectionMode):
        super().__init__(parent)

        self.title_label = SubtitleLabel(title)
        self.desc_label = QLabel(desc)

        self.inputer = ListWidget(self)
        self.inputer.addItems(texts)
        self.inputer.setSelectionMode(selection)
        self.inputer.setFixedSize(parent.size() * 0.65)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.desc_label)
        self.viewLayout.addWidget(self.inputer)

        self.cancelButton.hide()

def get_values_bylist(parent: QWidget, title: str, desc: str, texts: list[str], selection: QListWidget.SelectionMode) -> list[str]:
    dialog = ListDialog(parent, title, desc, texts, selection)
    dialog.exec()

    return [item.text() for item in dialog.inputer.selectedItems()]