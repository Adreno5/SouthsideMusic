import io

from core.backend import getBackend
from PySide6.QtWidgets import QLabel, QListWidget, QWidget
from core.i18n import bindText, tr
from core.downloader import asyncTask
from PySide6.QtGui import QImage, QPixmap
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

        self.title_label = SubtitleLabel('')
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
        self._login_pending = False

        self.yesButton.hide()

    def makeImage(self, qrcode_url: str) -> None:
        self.qrlabel.show()
        result: dict[str, QImage] = {}

        def _make() -> None:
            io_ = io.BytesIO()
            qrcode.make(qrcode_url).save(io_)
            result['image'] = QImage.fromData(io_.getvalue())

        def _apply() -> None:
            image = result.get('image')
            if image is not None and not image.isNull():
                self.qrlabel.setPixmap(
                    QPixmap.fromImage(image).scaled(self.qrlabel.size())
                )
            self.is_btn.setEnabled(True)

        asyncTask(_make, (), self, _apply)

    def login(self):
        if self._login_pending:
            return
        self._login_pending = True
        self.is_btn.setEnabled(False)
        self.errlabel.hide()
        result: dict[str, int] = {}

        def _check() -> None:
            try:
                result['code'] = getBackend().checkLoginQRCode(self.key)
            except Exception as error:
                self.logger.exception(error)
                result['code'] = 0

        def _finish() -> None:
            self._login_pending = False
            self.is_btn.setEnabled(True)
            code = result.get('code', 0)
            if code == 803:
                self.logger.info('Logined in successfully')
                self.accept()
            elif code == 8821:
                self.errlabel.setText(tr('dialogs.login_anomaly_risk_control'))
                self.errlabel.show()
            elif code == 800:
                self.errlabel.setText(tr('dialogs.qr_code_expired_or_not_exist'))
                self.errlabel.show()

        asyncTask(_check, (), self, _finish)


class CookieLoginDialog(MessageBoxBase):
    def __init__(self, parent):
        super().__init__(parent)

        title_label = TitleLabel()
        bindText(title_label, 'dialogs.login_via_cookie')
        self.viewLayout.addWidget(title_label)

        desc_label = QLabel()
        bindText(desc_label, 'dialogs.cookie_login_desc')
        self.viewLayout.addWidget(desc_label)

        self.cookie_input = LineEdit(self)
        self.cookie_input.setPlaceholderText(tr('dialogs.enter_music_u_cookie'))
        self.cookie_input.setFixedWidth(parent.width() * 0.7)
        self.viewLayout.addWidget(self.cookie_input)

        self.errlabel = QLabel()
        self.errlabel.hide()
        self.errlabel.setStyleSheet('color: red;')
        self.viewLayout.addWidget(self.errlabel)

        self.yesButton.setText(tr('dialog.yes'))
        self.cancelButton.setText(tr('dialog.no'))
