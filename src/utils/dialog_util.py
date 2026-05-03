import io

from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QVBoxLayout, QWidget
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from pyncm import apis
import qrcode
from qfluentwidgets import (
    LineEdit,
    ListWidget,
    MessageBoxBase,
    PrimaryPushButton,
    SubtitleLabel,
    TextEdit,
    TitleLabel,
)
import requests


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


def get_text_lineedit(title: str, desc: str, place: str, parent: QWidget):
    dialog = LineinputDialog(parent, title, desc, place)
    response = dialog.exec()

    return dialog.inputer.text() if response else ""


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
    def __init__(self, parent, title: str, desc: str, texts: list[str]):
        super().__init__(parent)

        self.title_label = SubtitleLabel(title)
        self.desc_label = QLabel(desc)

        self.inputer = ListWidget(self)
        self.inputer.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.inputer.addItems(texts)
        self.inputer.setFixedSize(parent.size() * 0.5)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.desc_label)
        self.viewLayout.addWidget(self.inputer)


def get_value_bylist(
    parent: QWidget, title: str, desc: str, texts: list[str]
) -> str | None:
    dialog = ListDialog(parent, title, desc, texts)
    reply = dialog.exec()
    selected = dialog.inputer.selectedItems()[0].text()

    if reply and selected:
        return selected
    else:
        return None


class QRCodeLoginDialog(MessageBoxBase):
    def __init__(self, parent, qrcode_url: str, key: str, logger):
        super().__init__(parent)

        self.key = key
        self.logger = logger

        self.viewLayout.addWidget(TitleLabel("Login via QRCode"))
        self.viewLayout.addWidget(
            QLabel(
                "use your CloudMusic app to scan the QRCode and click 'I scanned' button"
            )
        )

        self.qrlabel = QLabel()
        self.qrlabel.setFixedSize(parent.height() * 0.5, parent.height() * 0.5)

        self.viewLayout.addWidget(self.qrlabel)

        self.is_btn = PrimaryPushButton("I scanned")
        self.is_btn.clicked.connect(self.login)
        self.viewLayout.addWidget(self.is_btn)
        self.is_btn.setEnabled(False)

        self.errlabel = QLabel()
        self.errlabel.hide()
        self.errlabel.setStyleSheet("color: red;")
        self.viewLayout.addWidget(self.errlabel)

        self.makeImage(qrcode_url)

        self.yesButton.hide()

    def makeImage(self, qrcode_url: str) -> None:
        self.qrlabel.show()

        io_ = io.BytesIO()
        qrcode.make(qrcode_url).save(io_)
        io_.seek(0)

        qimage = QImage.fromData(io_.read())
        self.qrlabel.setPixmap(QPixmap.fromImage(qimage).scaled(self.qrlabel.size()))

        self.is_btn.setEnabled(True)

    def login(self):
        self.errlabel.hide()

        rsp: dict = apis.login.LoginQrcodeCheck(self.key)  # type: ignore
        if rsp["code"] == 803:
            self.logger.info("Logined in successfully")
            apis.login.WriteLoginInfo(
                apis.login.GetCurrentLoginStatus(),  # type: ignore
            )

            self.accept()
        elif rsp["code"] == 8821:
            self.errlabel.setText("Login anomaly risk control")
            self.errlabel.show()
        elif rsp["code"] == 800:
            self.errlabel.setText("QRCode expired or not exist")
            self.errlabel.show()
