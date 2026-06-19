import io

from imports import QLabel, QListWidget, QWidget, bindText, tr
from imports import QImage, QPixmap
from pyncm import apis
import qrcode
from qfluentwidgets import (
    LineEdit,
    MessageBoxBase,
    PrimaryPushButton,
    SubtitleLabel,
    TextEdit,
    TitleLabel,
)
from views.list_widget import SListWidget


class LineInputDialog(MessageBoxBase):
    def __init__(self, parent, title: str, desc: str, place: str):
        super().__init__(parent)

        self.title_label = SubtitleLabel()
        self.desc_label = QLabel()
        bindText(self.title_label, title)
        bindText(self.desc_label, desc)

        self.inputer = LineEdit(self)
        self.inputer.returnPressed.connect(self.accept)
        self.inputer.setPlaceholderText(tr(place))
        self.inputer.setFixedWidth(parent.width() * 0.7)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.desc_label)
        self.viewLayout.addWidget(self.inputer)


def getTextLineedit(title: str, desc: str, place: str, parent: QWidget | None):
    dialog = LineInputDialog(parent, title, desc, place)
    response = dialog.exec()

    return dialog.inputer.text() if response else ''


class TextEditDialog(MessageBoxBase):
    def __init__(self, parent, title: str, desc: str, place: str):
        super().__init__(parent)

        self.title_label = SubtitleLabel()
        self.desc_label = QLabel()
        bindText(self.title_label, title)
        bindText(self.desc_label, desc)

        self.inputer = TextEdit(self)
        self.inputer.setPlaceholderText(tr(place))
        self.inputer.setFixedSize(parent.size() * 0.65)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.desc_label)
        self.viewLayout.addWidget(self.inputer)

        self.cancelButton.hide()


def getTextTextedit(title: str, desc: str, place: str, parent: QWidget):
    dialog = TextEditDialog(parent, title, desc, place)
    dialog.exec()

    return dialog.inputer.toPlainText()


class ListDialog(MessageBoxBase):
    def __init__(self, parent, title: str, desc: str, texts: list[str]):
        super().__init__(parent)

        self.title_label = SubtitleLabel()
        self.desc_label = QLabel()
        bindText(self.title_label, title)
        bindText(self.desc_label, desc)

        self.inputer = SListWidget(self)
        self.inputer.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.inputer.addItems(texts)
        self.inputer.setFixedSize(parent.size() * 0.5)

        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(self.desc_label)
        self.viewLayout.addWidget(self.inputer)


def getValueBylist(
    parent: QWidget, title: str, desc: str, texts: list[str]
) -> str | None:
    dialog = ListDialog(parent, title, desc, texts)
    reply = dialog.exec()
    try:
        selected = dialog.inputer.selectedItems()[0].text()
    except Exception:
        return None

    if reply and selected:
        return selected
    else:
        return None


class QRCodeLoginDialog(MessageBoxBase):
    def __init__(self, parent, qrcode_url: str, key: str, logger):
        super().__init__(parent)

        self.key = key
        self.logger = logger

        title_label = TitleLabel()
        bindText(title_label, 'dialogs.login_via_qr_code')
        self.viewLayout.addWidget(title_label)
        desc_label = QLabel()
        bindText(
            desc_label,
            'dialogs.use_your_cloudmusic_app_to_scan_the_qr_code_and_click_i_scanned_button',
        )
        self.viewLayout.addWidget(desc_label)

        self.qrlabel = QLabel()
        self.qrlabel.setFixedSize(parent.height() * 0.5, parent.height() * 0.5)

        self.viewLayout.addWidget(self.qrlabel)

        self.is_btn = PrimaryPushButton('')
        bindText(self.is_btn, 'dialogs.i_scanned')
        self.is_btn.clicked.connect(self.login)
        self.viewLayout.addWidget(self.is_btn)
        self.is_btn.setEnabled(False)

        self.errlabel = QLabel()
        self.errlabel.hide()
        self.errlabel.setStyleSheet('color: red;')
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

        rsp: dict = apis.login.loginQrcodeCheck(self.key)  # type: ignore
        if rsp['code'] == 803:
            self.logger.info('Logined in successfully')
            apis.login.writeLoginInfo(
                apis.login.getCurrentLoginStatus(),  # type: ignore
            )

            self.accept()
        elif rsp['code'] == 8821:
            self.errlabel.setText(tr('dialogs.login_anomaly_risk_control'))
            self.errlabel.show()
        elif rsp['code'] == 800:
            self.errlabel.setText(tr('dialogs.qr_code_expired_or_not_exist'))
            self.errlabel.show()
