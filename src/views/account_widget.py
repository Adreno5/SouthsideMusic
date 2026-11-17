from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests

from core.backend import getBackend
from core.config import cfg, scheduleConfigSave
from core.downloader import asyncTask
from core.dialogs import (
    CookieLoginDialog,
    QRCodeLoginDialog,
    getTextLineedit,
    getValueBylist,
)
from core.i18n import tr
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPixmap, Qt
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
from core.image import loadImageFromBytes, loadImageFromPath
from core.models import BackendSessionSnapshot

if TYPE_CHECKING:
    from core.app_context import AppContext

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoginInformations:
    nickname: str
    avatar: QImage | None


def fetchLoginInformations() -> LoginInformations:
    backend = getBackend()
    account = None
    avatar: QImage | None = None

    try:
        if not backend.loggedIn() and os.path.exists('images/avatar.png'):
            os.remove('images/avatar.png')
    except Exception as error:
        _logger.warning(f'Failed to remove cached avatar: {error}')

    try:
        account = backend.getAccountInfo()
        if account.avatar_url:
            _logger.debug(f'{account.avatar_url=}')
            avatar_data = requests.get(account.avatar_url, timeout=(3.05, 8)).content
            with open('images/avatar.tmp', 'wb') as file:
                file.write(avatar_data)
            os.replace('images/avatar.tmp', 'images/avatar.png')
            avatar = loadImageFromBytes(avatar_data)
        elif os.path.exists('images/avatar.png'):
            avatar = loadImageFromPath('images/avatar.png')
    except Exception as error:
        _logger.warning(f'Failed to fetch user detail or avatar: {error}')

    if account is not None and account.nickname.strip():
        return LoginInformations(account.nickname.strip(), avatar)
    return LoginInformations('Anonymous User', avatar)


class AccountWidget(QWidget):
    loginChanged = Signal()

    def __init__(self, parent: QWidget, ctx: AppContext) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._logger = logging.getLogger(__name__)
        self._mwindow = parent
        self._nickname = 'Anonymous User'
        self._refresh_seq = 0
        self._login_busy = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        account_layout = QHBoxLayout()
        account_layout.setContentsMargins(5, 0, 0, 0)
        account_layout.setSpacing(6)
        self.avatar_widget = AvatarWidget(
            str(Path('./images/def_avatar.png').resolve())
        )
        self._default_avatar = QPixmap(str(Path('./images/def_avatar.png').resolve()))
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

    def applyLoginInformations(
        self,
        nickname: str,
        avatar: QImage | None = None,
    ) -> None:
        self._nickname = nickname
        self.nickname_label.setText(
            tr('main_window.anonymous_user')
            if nickname == 'Anonymous User'
            else nickname
        )
        pixmap = (
            QPixmap.fromImage(avatar) if avatar is not None else self._default_avatar
        )
        if not pixmap.isNull():
            self.avatar_widget.setPixmap(pixmap)

    def refreshLoginInformations(self) -> None:
        self._refresh_seq += 1
        seq = self._refresh_seq

        def _load() -> None:
            info = fetchLoginInformations()
            self.ctx.addScheduledTask(self._applyLoginInformations, seq, info)

        threading.Thread(target=_load, daemon=True).start()

    def _applyLoginInformations(self, seq: int, info: LoginInformations) -> None:
        if seq != self._refresh_seq:
            return
        self.applyLoginInformations(info.nickname, info.avatar)

    def logout(self) -> None:
        if self._login_busy:
            return
        self._login_busy = True
        result: dict[str, object] = {}

        def _logout() -> None:
            try:
                result['snapshot'] = getBackend().logout()
            except Exception as error:
                result['error'] = error

        def _finish() -> None:
            self._login_busy = False
            error = result.get('error')
            snapshot = result.get('snapshot')
            if error is not None or snapshot is None:
                self._logger.exception(error)
                InfoBar.error(
                    'Error',
                    str(error),
                    parent=self._mwindow,
                    duration=5000,
                )
                return
            cfg.session = snapshot.session  # type: ignore[union-attr]
            cfg.login_status = snapshot.login_status  # type: ignore[union-attr]
            scheduleConfigSave()
            self.applyLoginInformations('Anonymous User')
            self.refreshLoginInformations()
            self.loginChanged.emit()
            InfoBar.success(
                '',
                tr('main_window.logout_successful'),
                parent=self._mwindow,
                duration=5000,
            )

        asyncTask(_logout, (), self._mwindow, _finish)

    def login(self) -> None:
        if self._login_busy:
            return
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
            self._loginByQRCode()
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
            self._loginByCellphone(phone)
        elif method == 'Cookie':
            self._logger.info('start logging in(via cookie)')
            msgbox = CookieLoginDialog(self._mwindow)
            if not msgbox.exec():
                return
            music_u = msgbox.cookie_input.text().strip()
            if not music_u:
                return
            self._loginByCookie(music_u)

    def _loginByQRCode(self) -> None:
        self._logger.info('start logging in(via QRCode)')
        self._login_busy = True
        result: dict[str, object] = {}

        def _request() -> None:
            try:
                result['info'] = getBackend().createLoginQRCode()
            except Exception as error:
                result['error'] = error

        def _show() -> None:
            self._login_busy = False
            error = result.get('error')
            qrcode_info = result.get('info')
            if error is not None or qrcode_info is None:
                InfoBar.error('Error', str(error), parent=self._mwindow)
                return
            self._logger.debug(f'{qrcode_info.key=}')  # type: ignore[union-attr]
            self._logger.debug(f'{qrcode_info.url=}')  # type: ignore[union-attr]
            msgbox = QRCodeLoginDialog(
                self._mwindow,
                qrcode_info.url,  # type: ignore[union-attr]
                qrcode_info.key,  # type: ignore[union-attr]
                logging,
            )
            if msgbox.exec():
                self._completeLogin('QR Code', None)

        asyncTask(_request, (), self._mwindow, _show)

    def _loginByCellphone(self, phone: str) -> None:
        self._login_busy = True
        result: dict[str, bool] = {}

        def _send() -> None:
            result['sent'] = getBackend().sendCellphoneVerificationCode(phone, 86)

        def _sent() -> None:
            self._login_busy = False
            if not result.get('sent', False):
                InfoBar.error(
                    'Error',
                    'The verification code could not be sent.',
                    parent=self._mwindow,
                )
                return
            self._requestPhoneCode(phone)

        asyncTask(_send, (), self._mwindow, _sent)

    def _requestPhoneCode(self, phone: str) -> None:
        captcha = getTextLineedit(
            tr('main_window.verification_code_sent'),
            tr('main_window.enter_the_verification_code'),
            'xxxx',
            self._mwindow,
        )
        if len(captcha) != 4:
            QTimer.singleShot(0, lambda: self._requestPhoneCode(phone))
            return
        self._login_busy = True
        result: dict[str, bool] = {}

        def _verify() -> None:
            result['verified'] = getBackend().verifyCellphoneVerificationCode(
                phone,
                captcha,
                86,
            )

        def _verified() -> None:
            self._login_busy = False
            if not result.get('verified', False):
                InfoBar.error(
                    'Error',
                    'The verification code is incorrect.',
                    parent=self._mwindow,
                )
                QTimer.singleShot(0, lambda: self._requestPhoneCode(phone))
                return
            self._finishPhoneLogin(phone, captcha)

        asyncTask(_verify, (), self._mwindow, _verified)

    def _finishPhoneLogin(self, phone: str, captcha: str) -> None:
        self._login_busy = True
        result: dict[str, object] = {}

        def _login() -> None:
            try:
                result['snapshot'] = getBackend().loginViaCellphone(phone, captcha, 86)
            except Exception as error:
                result['error'] = error

        def _finish() -> None:
            self._login_busy = False
            snapshot = result.get('snapshot')
            error = result.get('error')
            if error is not None or snapshot is None:
                InfoBar.error('Error', str(error), parent=self._mwindow)
                return
            self._completeLogin('Cell Phone', snapshot)  # type: ignore[arg-type]

        asyncTask(_login, (), self._mwindow, _finish)

    def _loginByCookie(self, music_u: str) -> None:
        self._login_busy = True
        result: dict[str, object] = {}

        def _login() -> None:
            try:
                result['snapshot'] = getBackend().loginViaCookie(music_u)
            except Exception as error:
                result['error'] = error

        def _finish() -> None:
            self._login_busy = False
            snapshot = result.get('snapshot')
            error = result.get('error')
            if error is not None or snapshot is None:
                InfoBar.error('Error', str(error), parent=self._mwindow)
                return
            self._completeLogin('Cookie', snapshot)  # type: ignore[arg-type]

        asyncTask(_login, (), self._mwindow, _finish)

    def _completeLogin(
        self,
        method: str,
        snapshot: BackendSessionSnapshot | None,
    ) -> None:
        self._login_busy = True
        result: dict[str, object] = {'snapshot': snapshot}

        def _capture() -> None:
            if snapshot is None:
                result['snapshot'] = BackendSessionSnapshot(
                    session=getBackend().dumpSession(),
                    login_status=getBackend().getCurrentLoginStatus(),
                )

        def _apply() -> None:
            self._login_busy = False
            current = result.get('snapshot')
            if not isinstance(current, BackendSessionSnapshot):
                return
            cfg.session = current.session
            cfg.login_status = current.login_status
            cfg.login_method = {
                'QR Code': 'QR code',
                'Cell Phone': 'cell phone',
                'Cookie': 'cookie',
            }.get(method, 'anonymous')  # type: ignore[assignment]
            scheduleConfigSave()
            InfoBar.success(
                tr('main_window.login_successful'),
                tr('main_window.logged_in_via_method_method', method=tr(method)),
                parent=self._mwindow,
                duration=5000,
            )
            self.refreshLoginInformations()
            self.loginChanged.emit()

        if snapshot is None:
            asyncTask(_capture, (), self._mwindow, _apply)
        else:
            _apply()
