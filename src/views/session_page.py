from __future__ import annotations

import logging

import os

from core.app_context import AppContext
from imports import LANGUAGE_CHANGED, Qt
from imports import QPixmap
from imports import QHBoxLayout, QLabel, QVBoxLayout, QWidget, bindText, event_bus, tr
from services.events.events import (
    MWINDOW_REFRESH_FOLDERS,
)
from qfluentwidgets import MessageBox, PrimaryPushButton, SubtitleLabel, TitleLabel

from core.dialogs import QRCodeLoginDialog, getValueBylist, getTextLineedit
from core.icons import bindIcon
import requests
from core.config import cfg
import pyncm as ncm
from pyncm import apis


class SessionPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        self._nickname = 'Anonymous User'
        self._vip_level = 0
        lw = ctx.launch_window
        if lw:
            lw.top('Initializing session page...')
        self.setObjectName('session_page')

        if lw:
            lw.top('  creating user avatar and nickname')
        self.nickname = TitleLabel()
        self.avatar = QLabel()
        global_layout = QVBoxLayout()
        user_layout = QHBoxLayout()
        user_layout.addWidget(
            self.avatar,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        user_layout.addWidget(
            self.nickname,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.avatar.setFixedSize(self.nickname.height() - 3, self.nickname.height() - 3)
        global_layout.addLayout(user_layout)

        if lw:
            lw.top('  creating VIP level label')
        bottom_layout = QHBoxLayout()
        self.vip = SubtitleLabel(tr('session_page.vip_level_loading'))
        bottom_layout.addWidget(
            self.vip, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )

        if lw:
            lw.top('  creating login button')
        self.login_btn = PrimaryPushButton('')
        bindText(self.login_btn, 'session_page.login')
        bindIcon(self.login_btn, 'login', 'light')
        self.login_btn.clicked.connect(self.login)
        bottom_layout.addWidget(
            self.login_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
        )
        global_layout.addLayout(bottom_layout)
        self.setLayout(global_layout)

        if lw:
            lw.top('  loading user info from server')
        self.refreshInformations()
        event_bus.subscribe(LANGUAGE_CHANGED, self.updateLanguage)

    @property
    def _mwindow(self):
        return self.ctx.main_window

    def _dialog_parent(self) -> QWidget:
        return self._mwindow or self

    def updateLanguage(self) -> None:
        self.nickname.setText(
            tr('session_page.anonymous_user')
            if self._nickname == 'Anonymous User'
            else self._nickname
        )
        self.vip.setText(tr('session_page.vip_level_value', value=self._vip_level))

    def refreshInformations(self):
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
        self.nickname.setText(
            tr('session_page.anonymous_user')
            if nickname == 'Anonymous User'
            else nickname
        )

        vip_level = 0
        if session is not None:
            try:
                vip = getattr(session, 'vipType', 0)
                if isinstance(vip, (int, float)):
                    vip_level = int(vip)
            except Exception as e:
                self._logger.warning(f'Failed to get vipType: {e}')
        self._vip_level = vip_level
        self.vip.setText(tr('session_page.vip_level_value', value=vip_level))

        if not os.path.exists('images/avatar.png'):
            pixmap = QPixmap('images/def_avatar.png')
        else:
            pixmap = QPixmap('images/avatar.png')
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.height() * 0.4,  # type: ignore
                self.height() * 0.4,  # type: ignore
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.avatar.setPixmap(scaled)

    def login(self):
        parent = self._dialog_parent()
        method = getValueBylist(
            parent,
            tr('session_page.login'),
            tr('session_page.choose_method_to_log_into_an_account'),
            [
                tr('session_page.qr_code'),
                tr('session_page.cell_phone'),
                tr('session_page.anonymous'),
            ],
        )
        if method is None:
            return
        method_map = {
            tr('session_page.qr_code'): 'QR Code',
            tr('session_page.cell_phone'): 'Cell Phone',
            tr('session_page.anonymous'): 'Anonymous',
        }
        method = method_map.get(method, method)

        if method == 'Anonymous':
            apis.login.loginViaAnonymousAccount()
            cfg.session = ncm.dumpSessionAsString(ncm.getCurrentSession())
        elif method == 'QR Code':
            self._logger.info('start logging in(via QRCode)')

            key: str = apis.login.loginQrcodeUnikey()['unikey']  # type: ignore
            self._logger.debug(f'{key=}')

            url = apis.login.getLoginQRCodeUrl(key)
            self._logger.debug(f'{url=}')

            msgbox = QRCodeLoginDialog(parent, url, key, logging)
            if msgbox.exec():
                cfg.session = ncm.dumpSessionAsString(ncm.getCurrentSession())
                cfg.login_status = apis.login.getCurrentLoginStatus()  # type: ignore
                cfg.login_method = 'QR code'
        elif method == 'Cell Phone':
            self._logger.info('start logging in(via cell phone)')
            phone = getTextLineedit(
                tr('session_page.login'),
                tr('session_page.enter_your_cell_phone_number'),
                '1xxxxxxxxxx',
                parent,
            )
            if not phone:
                return

            result = apis.login.setSendRegisterVerificationCodeViaCellphone(phone, 86)
            assert result.get('code', 0) == 200, 'Invaild response'  # type: ignore
            while True:
                captcha = getTextLineedit(
                    tr('session_page.verification_code_sent'),
                    tr('session_page.enter_the_verification_code'),
                    'xxxx',
                    parent,
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

        from qfluentwidgets import InfoBar

        InfoBar.success(
            tr('session_page.login_successful'),
            tr('session_page.logged_in_via_method_method', method=tr(method)),
            parent=parent,
            duration=5000,
        )

        self.refreshInformations()
        event_bus.emit(MWINDOW_REFRESH_FOLDERS)

    def showSession(self):
        s = ncm.dumpSessionAsString(ncm.getCurrentSession())

        msgbox = MessageBox(tr('session_page.session'), s, self._dialog_parent())
        msgbox.exec()
