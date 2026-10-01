import logging
import os
from typing import TYPE_CHECKING

import requests
import shiboken6

from core.backend import getBackend
from imports import (
    AvatarWidget,
    CaptionLabel,
    FluentIcon,
    InfoBar,
    Path,
    PrimaryToolButton,
    QHBoxLayout,
    QPixmap,
    QSizePolicy,
    QSpacerItem,
    QTimer,
    QVBoxLayout,
    QWidget,
    Qt,
    TextEdit,
    TitleLabel,
    bindText,
    BodyLabel,
    SubtitleLabel,
    CardWidget,
)
from views.list_widget import SScrollArea
from views.translation_handler import TranslationHandler
from core.i18n import tr
from core.qt_utils import removeWidgets
from core.downloader import asyncTask
from core.models import BeReplyComment, Comment, CommentInfo, UserInfo

if TYPE_CHECKING:
    from core.app_context import AppContext


class CommentCard(QWidget):
    def __init__(
        self,
        comment: Comment | BeReplyComment,
        ctx: AppContext,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.main_id = comment.id
        self.comment_ids = {comment.id}
        self.replies: list[Comment] = []
        self.global_layout = QVBoxLayout()
        self.layout_ = QHBoxLayout()

        left = QVBoxLayout()
        self.avatar = AvatarWidget()
        self.avatar.setRadius(16)
        left.addWidget(self.avatar)
        left.addSpacerItem(
            QSpacerItem(
                0,
                0,
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Expanding,
            )
        )
        self.layout_.addLayout(left)

        right = QVBoxLayout()
        right.addWidget(SubtitleLabel(comment.publisher.nickname))
        right.addWidget(BodyLabel(comment.content))

        self.layout_.addLayout(right)
        self.global_layout.addLayout(self.layout_)
        self.setLayout(self.global_layout)

        self._loadAvatar(self.avatar, comment.publisher, 32)

    def addReply(self, comment: Comment) -> bool:
        if comment.id in self.comment_ids:
            return False

        self.comment_ids.add(comment.id)
        self.replies.append(comment)
        card = CardWidget()
        reply = QHBoxLayout()
        reply.addSpacerItem(
            QSpacerItem(
                20,
                0,
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Ignored,
            )
        )
        middle = QVBoxLayout()
        reply_avatar = AvatarWidget()
        reply_avatar.setRadius(14)
        middle.addWidget(reply_avatar)
        middle.addSpacerItem(
            QSpacerItem(
                0,
                0,
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Expanding,
            )
        )
        reply.addLayout(middle)

        reply_right = QVBoxLayout()
        reply_right.addWidget(SubtitleLabel(comment.publisher.nickname))
        reply_right.addWidget(CaptionLabel(comment.content))
        reply.addLayout(reply_right)
        card.setLayout(reply)
        self.global_layout.addWidget(card)

        self._loadAvatar(reply_avatar, comment.publisher, 28)
        return True

    def _loadAvatar(
        self,
        avatar: AvatarWidget,
        publisher: UserInfo,
        size: int,
    ) -> None:
        avatar_path = (
            Path(f'.\\data\\image\\u_avatar{publisher.id}').absolute().resolve()
        )
        pixmap = QPixmap()

        def _final(image: QPixmap) -> None:
            scaled = image.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

            def _apply() -> None:
                if shiboken6.isValid(avatar):
                    avatar.setImage(scaled)

            self.ctx.addScheduledTask(_apply)

        if avatar_path.is_file():
            pixmap.loadFromData(avatar_path.read_bytes())
            _final(pixmap)
            return

        def _download() -> None:
            data = requests.get(publisher.avatar_url).content
            pixmap.loadFromData(data)
            _final(pixmap)
            avatar_path.write_bytes(data)

        asyncTask(_download, (), self)


class CommentsPage(SScrollArea):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx

        widget = QWidget()
        layout = QVBoxLayout()
        widget.setLayout(layout)

        self.page: int = 1
        self.cursor: str = '-1'
        self.loading = False
        self.can_load_more = True
        self.comment_card_map: dict[str, CommentCard] = {}

        title_label = TitleLabel('')
        bindText(title_label, 'comments_page.title')
        layout.addWidget(title_label)

        top_layout = QHBoxLayout()
        self.avatar_widget = AvatarWidget(
            str(Path('./images/def_avatar.png').resolve())
        )
        self.avatar_widget.setRadius(18)
        top_layout.addWidget(self.avatar_widget)
        self.inputer = TextEdit()
        self.inputer.textChanged.connect(self.recalculatePayload)
        topright_layout = QVBoxLayout()
        topright_layout.addWidget(self.inputer)
        self.send_button = PrimaryToolButton(FluentIcon.SEND)
        toprightbottom_layout = QHBoxLayout()
        toprightbottom_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        )
        self.remain_label = CaptionLabel('140')
        toprightbottom_layout.addWidget(self.remain_label)
        toprightbottom_layout.addWidget(self.send_button)
        topright_layout.addLayout(toprightbottom_layout)
        self.send_button.clicked.connect(self.send)
        top_layout.addLayout(topright_layout)
        place_handler = TranslationHandler()
        bindText(place_handler, 'comments_page.say_sth')
        place_handler.textChanged.connect(lambda t: self.inputer.setPlaceholderText(t))
        self.inputer.setPlaceholderText(tr('comments_page.say_sth'))
        layout.addLayout(top_layout)

        self.comments_layout = QVBoxLayout()
        layout.addLayout(self.comments_layout)

        layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        )

        self.setWidget(widget)
        self.setWidgetResizable(True)

        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self.checkRect)
        self.check_timer.start(50)

    def _getTextPayload(self, text: str) -> int:
        units = sum(2 if '\u4e00' <= char <= '\u9fff' else 1 for char in text)
        return (units + 1) // 2

    def recalculatePayload(self):
        text = self.inputer.toPlainText()
        payload = self._getTextPayload(text)
        self.remain_label.setText(str(int(140 - payload)))
        self.send_button.setEnabled(payload <= 140)

    def checkRect(self) -> None:
        bar = self.verticalScrollBar()
        if (
            self.ctx.main_window
            and bar.value() >= bar.maximum() - 5
            and not self.loading
            and self.ctx.main_window.contents_widget.currentWidget() == self
            and self.can_load_more
        ):
            self._logger.info('load more comments')
            self.loadMore()

    def loadComments(self) -> None:
        self.page = 1
        self.cursor = '-1'
        self.can_load_more = True
        removeWidgets(self.comments_layout)
        self.comment_card_map.clear()
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        self.loadMore()

    def _addComments(self, comments: list[Comment]) -> int:
        added_count = 0
        for comment in comments:
            parent = comment.be_replied[0] if comment.be_replied else None
            if parent is None:
                if comment.id not in self.comment_card_map:
                    card = CommentCard(comment, self.ctx)
                    self.comments_layout.addWidget(card)
                    self.comment_card_map[comment.id] = card
                    added_count += 1
                continue

            parent_card = self.comment_card_map.get(parent.id)
            if parent_card is None:
                parent_card = CommentCard(parent, self.ctx)
                self.comments_layout.addWidget(parent_card)
                self.comment_card_map[parent.id] = parent_card
                added_count += 1

            current_card = self.comment_card_map.get(comment.id)
            if current_card is not None and current_card is not parent_card:
                added_count += parent_card.addReply(comment)
                for reply in current_card.replies:
                    added_count += parent_card.addReply(reply)
                for comment_id in current_card.comment_ids:
                    self.comment_card_map[comment_id] = parent_card
                self.comments_layout.removeWidget(current_card)
                current_card.deleteLater()
            else:
                added_count += parent_card.addReply(comment)

            self.comment_card_map[comment.id] = parent_card

        return added_count

    def loadMore(self) -> None:
        if self.loading or not self.can_load_more:
            return

        current = self.ctx.playing_manager.current_song
        if current is None:
            return

        self.loading = True
        request_cursor = self.cursor

        def _load() -> None:
            info = getBackend().getComments(
                current.id, self.page, 20, 'time', self.cursor
            )

            def _loaded(info: CommentInfo) -> None:
                added_count = self._addComments(info.comments)

                self.cursor = info.cursor
                cursor_advanced = info.cursor != request_cursor
                self.can_load_more = (
                    bool(info.comments)
                    and self.page * 20 < info.total
                    and (added_count > 0 or cursor_advanced)
                )
                self.page += 1

            self.ctx.addScheduledTask(lambda: _loaded(info))

        asyncTask(
            _load,
            (),
            self,
            finished=lambda: setattr(self, 'loading', False),
        )

    def send(self):
        backend = getBackend()
        if not backend.getAccountInfo().logged_in:
            InfoBar.warning(
                'Error',
                "You' not logged in!",
                duration=3500,
                parent=self.ctx.main_window,
            )
            return

        current = self.ctx.playing_manager.current_song
        if not current:
            return
        backend.addComment(current.id, self.inputer.toPlainText())
        self.inputer.clear()

        InfoBar.success(
            'Success',
            'Added a comment successfully',
            duration=3500,
            parent=self.ctx.main_window,
        )

    def refreshInformations(self):
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

        if not os.path.exists('images/avatar.png'):
            pixmap = QPixmap('./images/def_avatar.png')
        else:
            pixmap = QPixmap('./images/avatar.png')
        if not pixmap.isNull():
            self.avatar_widget.setPixmap(pixmap)
