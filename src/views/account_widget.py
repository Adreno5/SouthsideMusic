from __future__ import annotations

import logging
import os

import requests

from core.app_context import AppContext
from core.backend import getBackend
from core.config import cfg, saveConfig
from core.dialogs import (
    CookieLoginDialog,
    QRCodeLoginDialog,
    getTextLineedit,
    getValueBylist,
)
from core.i18n import tr
from PySide6.QtCore import Signal
from PySide6.QtGui import QMouseEvent, QPixmap, Qt
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QSpacerItem, QWidget
from qfluentwidgets import (
    Action,
    AvatarWidget,
    BodyLabel,
    FluentIcon,
    MenuAnimationType,
    Path,
    RoundMenu,
)
from qfluentwidgets import InfoBar


class AccountWidget(QWidget):
    loginChanged = Signal()

    def __init__(self, parent: QWidget, ctx: AppContext) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._logger = logging.getLogger(__name__)
        self._mwindow = parent
        self._nickname = 'Anonymous User'

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        account_layout = QHBoxLayout()
        account_layout.setContentsMargins(5, 0, 0, 0)
        account_layout.setSpacing(6)
        self.avatar_widget = AvatarWidget(
            str(Path('./images/def_avatar.png').resolve())
        )
        account_layout.addWidget(self.avatar_widget)
        self.nickname_label = BodyLabel('')
        account_layout.addWidget(self.nickname_label)
        account_layout.addSpacerItem(
            QSpacerItem(
                0,
                0,
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )
        )
        self.setLayout(account_layout)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if getBackend().loggedIn():
            menu = RoundMenu(parent=self._mwindow)
            logout_ac = Action(tr('main_window.logout'))
            logout_ac.setIcon(FluentIcon.EMBED)
            logout_ac.triggered.connect(self.logout)
            menu.addActions([logout_ac])
            menu.exec(event.globalPos(), aniType=MenuAnimationType.FADE_IN_DROP_DOWN)

            event.accept()
        else:
            self.login()

    def refreshLoginInformations(self) -> None:
        if os.path.exists('images/avatar.png'):
            os.remove('images/avatar.png')

        backend = getBackend()
        account = None

        try:
            account = backend.getAccountInfo()
            if account.avatar_url:
                self._logger.debug(f'{account.avatar_url=}')
                avatar_url = account.avatar_url
                avatar_data = requests.get(avatar_url).content
                with open('images/avatar.png', 'wb') as f:
                    f.write(avatar_data)
        except Exception as e:
            self._logger.warning(f'Failed to fetch user detail or avatar: {e}')

        nickname = 'Anonymous User'
        if account is not None and account.nickname.strip():
            nickname = account.nickname.strip()
        self._nickname = nickname
        self.nickname_label.setText(
            tr('main_window.anonymous_user')
            if nickname == 'Anonymous User'
            else nickname
        )

        if not os.path.exists('images/avatar.png'):
            pixmap = QPixmap('./images/def_avatar.png')
        else:
            pixmap = QPixmap('./images/avatar.png')
        if not pixmap.isNull():
            self.avatar_widget.setPixmap(pixmap)

    def logout(self) -> None:
        snapshot = getBackend().logout()
        cfg.session = snapshot.session
        cfg.login_status = snapshot.login_status
        saveConfig()

        self.refreshLoginInformations()
        self.loginChanged.emit()
        InfoBar.success(
            '',
            tr('main_window.logout_successful'),
            parent=self._mwindow,
            duration=5000,
        )

    def login(self) -> None:
        method = getValueBylist(
            self._mwindow,
            tr('main_window.login'),
            tr('main_window.choose_method_to_log_into_an_account'),
            [
                tr('main_window.qr_code'),
                tr('main_window.cell_phone'),
                tr('main_window.cookie'),
            ],
        )
        if method is None:
            return
        method_map = {
            tr('main_window.qr_code'): 'QR Code',
            tr('main_window.cell_phone'): 'Cell Phone',
            tr('main_window.cookie'): 'Cookie',
        }
        method = method_map.get(method, method)

        if method == 'QR Code':
            self._logger.info('start logging in(via QRCode)')

            qrcode_info = getBackend().createLoginQRCode()
            self._logger.debug(f'{qrcode_info.key=}')
            self._logger.debug(f'{qrcode_info.url=}')

            msgbox = QRCodeLoginDialog(
                self._mwindow, qrcode_info.url, qrcode_info.key, logging
            )
            if msgbox.exec():
                cfg.session = getBackend().dumpSession()
                cfg.login_status = getBackend().getCurrentLoginStatus()
                cfg.login_method = 'QR code'
        elif method == 'Cell Phone':
            self._logger.info('start logging in(via cell phone)')
            phone = getTextLineedit(
                tr('main_window.login'),
                tr('main_window.enter_your_cell_phone_number'),
                '1xxxxxxxxxx',
                self._mwindow,
            )
            if not phone:
                return

            result = getBackend().sendCellphoneVerificationCode(phone, 86)
            assert result, 'Invaild response'
            while True:
                captcha = getTextLineedit(
                    tr('main_window.verification_code_sent'),
                    tr('main_window.enter_the_verification_code'),
                    'xxxx',
                    self._mwindow,
                )
                if len(captcha) != 4:
                    continue
                verified = getBackend().verifyCellphoneVerificationCode(
                    phone, captcha, 86
                )
                if verified:
                    break

            snapshot = getBackend().loginViaCellphone(phone, captcha, 86)
            cfg.session = snapshot.session
            cfg.login_status = snapshot.login_status
            cfg.login_method = 'cell phone'
        elif method == 'Cookie':
            self._logger.info('start logging in(via cookie)')
            msgbox = CookieLoginDialog(self._mwindow)
            if not msgbox.exec():
                return
            music_u = msgbox.cookie_input.text().strip()
            if not music_u:
                return
            snapshot = getBackend().loginViaCookie(music_u)
            cfg.session = snapshot.session
            cfg.login_status = snapshot.login_status
            cfg.login_method = 'cookie'

        InfoBar.success(
            tr('main_window.login_successful'),
            tr('main_window.logged_in_via_method_method', method=tr(method)),
            parent=self._mwindow,
            duration=5000,
        )

        self.refreshLoginInformations()
        self.loginChanged.emit()
