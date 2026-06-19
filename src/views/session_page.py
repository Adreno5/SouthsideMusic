from __future__ import annotations

import logging

import os

from core.app_context import AppContext
from imports import Qt
from imports import QPixmap
from imports import QHBoxLayout, QLabel, QVBoxLayout, QWidget, event_bus
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
        self.vip = SubtitleLabel('VIP Level: Loading...')
        bottom_layout.addWidget(
            self.vip, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )

        if lw:
            lw.top('  creating login button')
        self.login_btn = PrimaryPushButton('Login')
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

    @property
    def _mwindow(self):
        return self.ctx.main_window

    def _dialog_parent(self) -> QWidget:
        return self._mwindow or self

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
        self.nickname.setText(nickname)

        vip_level = 0
        if session is not None:
            try:
                vip = getattr(session, 'vipType', 0)
                if isinstance(vip, (int, float)):
                    vip_level = int(vip)
            except Exception as e:
                self._logger.warning(f'Failed to get vipType: {e}')
        self.vip.setText(f'VIP Level: {vip_level}')

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
            'Login',
            'choose method to log into an account',
            ['QR Code', 'Cell Phone', 'Anonymous'],
        )
        if method is None:
            return

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
                'Login', 'enter your cell phone number', '1xxxxxxxxxx', parent
            )
            if not phone:
                return

            result = apis.login.setSendRegisterVerificationCodeViaCellphone(phone, 86)
            assert result.get('code', 0) == 200, 'Invaild response'  # type: ignore
            while True:
                captcha = getTextLineedit(
                    'Verification Code Sent',
                    'enter the verification code',
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
            'Login successful',
            f'logged in via method {method}',
            parent=parent,
            duration=5000,
        )

        self.refreshInformations()
        event_bus.emit(MWINDOW_REFRESH_FOLDERS)

    def showSession(self):
        s = ncm.dumpSessionAsString(ncm.getCurrentSession())

        msgbox = MessageBox('Session', s, self._dialog_parent())
        msgbox.exec()
