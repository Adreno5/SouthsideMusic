from __future__ import annotations

import logging
import os

import requests

from core.backend import getBackend
from core.config import cfg, saveConfig
from core.dialogs import QRCodeLoginDialog, getTextLineedit, getValueBylist
from core.i18n import tr
from imports import (
    Action,
    AvatarWidget,
    BodyLabel,
    FluentIcon,
    MenuAnimationType,
    Path,
    QHBoxLayout,
    QMouseEvent,
    QPixmap,
    QSizePolicy,
    QSpacerItem,
    Qt,
    RoundMenu,
    Signal,
    QWidget,
)
from qfluentwidgets import InfoBar

import pyncm as ncm
from pyncm import apis


class AccountWidget(QWidget):
    loginChanged = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
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
        self.avatar_widget.setRadius(18)
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
        self.setFixedHeight(40)

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

        try:
            session = ncm.getCurrentSession()
        except Exception as e:
            self._logger.warning(f'Failed to get session: {e}')
            session = None

        try:
            login_status = apis.login.getCurrentLoginStatus()
            if (
                login_status
                and 'account' in login_status
                and 'id' in login_status['account']  # type: ignore
            ):
                detail = apis.user.getUserDetail(login_status['account']['id'])  # type: ignore
                self._logger.debug(f'{detail['profile']['avatarUrl']=}')  # type: ignore
                avatar_url = detail['profile']['avatarUrl']  # type: ignore
                avatar_data = requests.get(avatar_url).content
                with open('images/avatar.png', 'wb') as f:
                    f.write(avatar_data)
        except Exception as e:
            self._logger.warning(f'Failed to fetch user detail or avatar: {e}')

        nickname = 'Anonymous User'
        if session is not None:
            try:
                nick = getattr(session, 'nickname', None)
                if nick and isinstance(nick, str) and nick.strip():
                    nickname = nick.strip()
                if cfg.login_status:
                    nick = getattr(cfg.login_status.get('account'), 'userName', None)
                    if nick and isinstance(nick, str) and nick.strip():
                        nickname = nick.strip()
            except Exception as e:
                self._logger.warning(f'Failed to get nickname: {e}')
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
            scaled = pixmap.scaled(
                52,
                52,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.avatar_widget.setPixmap(scaled)

    def logout(self) -> None:
        apis.login.loginLogout()
        ncm.setCurrentSession(ncm.createNewSession())
        cfg.session = ncm.dumpSessionAsString(ncm.getCurrentSession())
        saveConfig()

        self.refreshLoginInformations()
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
            ],
        )
        if method is None:
            return
        method_map = {
            tr('main_window.qr_code'): 'QR Code',
            tr('main_window.cell_phone'): 'Cell Phone',
        }
        method = method_map.get(method, method)

        if method == 'QR Code':
            self._logger.info('start logging in(via QRCode)')

            key: str = apis.login.loginQrcodeUnikey()['unikey']  # type: ignore
            self._logger.debug(f'{key=}')

            url = apis.login.getLoginQRCodeUrl(key)
            self._logger.debug(f'{url=}')

            msgbox = QRCodeLoginDialog(self._mwindow, url, key, logging)
            if msgbox.exec():
                cfg.session = ncm.dumpSessionAsString(ncm.getCurrentSession())
                cfg.login_status = apis.login.getCurrentLoginStatus()  # type: ignore
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

            result = apis.login.setSendRegisterVerificationCodeViaCellphone(phone, 86)
            assert result.get('code', 0) == 200, 'Invaild response'  # type: ignore
            while True:
                captcha = getTextLineedit(
                    tr('main_window.verification_code_sent'),
                    tr('main_window.enter_the_verification_code'),
                    'xxxx',
                    self._mwindow,
                )
                if len(captcha) != 4:
                    continue
                verified = apis.login.getRegisterVerificationStatusViaCellphone(
                    phone, captcha, 86
                )
                if verified.get('code', 0) == 200:  # type: ignore
                    break

            apis.login.loginViaCellphone(phone, captcha=captcha, ctcode=86)

            csession = ncm.getCurrentSession()
            cfg.session = ncm.dumpSessionAsString(csession)
            cfg.login_status = apis.login.getCurrentLoginStatus()
            cfg.login_method = 'cell phone'

        InfoBar.success(
            tr('main_window.login_successful'),
            tr('main_window.logged_in_via_method_method', method=tr(method)),
            parent=self._mwindow,
            duration=5000,
        )

        self.refreshLoginInformations()
        self.loginChanged.emit()
