import hashlib
import os
import weakref

import requests
import shiboken6

from core.app_context import AppContext
from core.cache_cleanup import touchCacheFile
from core.downloader import asyncTask
from core.favorites import favorites_manager
from core.icons import SouthsideIcon
from core.image import loadImageFromBytes, loadImageFromPath
from core.models import (
    CloudFolderInfo,
    LocalFolderInfo,
    SongStorable,
    SearchCloudFolderInfo,
)
from PySide6.QtCore import QSize, Signal
from PySide6.QtGui import QAction, QContextMenuEvent, QImage, QMouseEvent, QPixmap, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import RoundMenu
from core.i18n import tr
from services.events import Events, event_bus
from qfluentwidgets import SubtitleLabel


def _setImage(label: QLabel, image: QImage) -> None:
    if not shiboken6.isValid(label) or image.isNull():
        return
    label.setPixmap(QPixmap.fromImage(image))


class LocalFolderCard(QWidget):
    clicked = Signal()
    imagePersisted = Signal(object)

    def __init__(
        self,
        folder: LocalFolderInfo,
        width: int,
        ctx: AppContext,
    ) -> None:
        super().__init__()
        self.setFixedSize(width, 52)
        self.folder = folder
        self._ctx = ctx

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.img_label = QLabel()
        self.img_label.setFixedSize(50, 50)
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.img_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.img_label)

        title_label = QLabel(folder.folder_name)
        f = title_label.font()
        f.setPointSize(10)
        title_label.setFont(f)
        title_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        title_label.font().setPointSize(16)
        title_label.setWordWrap(True)
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(title_label)

        self.imagePersisted.connect(self._loadFirstSongImage)
        event_bus.subscribe(Events.IMAGE_ASSET_PERSISTED, self._onImageAssetPersisted)
        self._loadFirstSongImage()
        favorites_manager.ensureFolderFirstImage(folder)

    def _onImageAssetPersisted(self, storable: SongStorable) -> None:
        songs = self.folder.songs
        if not songs:
            return
        if storable.id != songs[0].id:
            return
        self.imagePersisted.emit(storable)

    def _loadFirstSongImage(self, storable: SongStorable | None = None) -> None:
        if not shiboken6.isValid(self.img_label):
            return
        songs = self.folder.songs
        if not songs:
            return
        first = songs[0]
        if storable is not None:
            if storable.id != first.id:
                return
            first.image_cache_hash = storable.image_cache_hash
        target = QSize(self.img_label.size())
        result: dict[str, QImage] = {}
        card_ref = weakref.ref(self)

        def _read() -> None:
            try:
                result['image'] = loadImageFromBytes(first.getImageBytes(), target)
            except (FileNotFoundError, PermissionError):
                return

        def _apply() -> None:
            card = card_ref()
            if card is None or not shiboken6.isValid(card.img_label):
                return
            image = result.get('image')
            if image is not None and not image.isNull():
                card.img_label.setPixmap(QPixmap.fromImage(image))

        asyncTask(_read, (), self, _apply)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        return super().mousePressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = RoundMenu()
        rm_ac = QAction(SouthsideIcon.REMOVE.icon(), tr('folder_card.remove'))
        rm_ac.triggered.connect(
            lambda: event_bus.emit(Events.LOCAL_REMOVE_FOLDER, self)
        )
        rn_ac = QAction(SouthsideIcon.RENAME.icon(), tr('folder_card.rename'))
        rn_ac.triggered.connect(
            lambda: event_bus.emit(Events.LOCAL_RENAME_FOLDER, self)
        )
        addto_cloud = QAction(SouthsideIcon.ADD.icon(), tr('folder_card.add_to_cloud'))
        addto_cloud.triggered.connect(
            lambda: event_bus.emit(Events.LOCAL_ADD_TO_CLOUD, self)
        )
        menu.addActions([rm_ac, rn_ac, addto_cloud])
        menu.exec(event.globalPos())


class CloudFolderCard(QWidget):
    clicked = Signal()

    def __init__(self, folder: CloudFolderInfo, width, ctx: AppContext):
        super().__init__()
        self.setFixedSize(width, 52)
        self.folder = folder
        self._ctx = ctx

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.img_label = QLabel()
        self.img_label.setFixedSize(50, 50)
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.img_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.img_label)

        title_label = QLabel(folder.folder_name)
        f = title_label.font()
        f.setPointSize(10)
        title_label.setFont(f)
        title_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        title_label.font().setPointSize(16)
        title_label.setWordWrap(True)
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(title_label)

        self._loadCoverImage()

    def _loadCoverImage(self):
        if not self.folder.image_url:
            return
        hashed = hashlib.sha256(self.folder.image_url.encode()).hexdigest()
        file = os.path.join('data', 'cover', hashed)
        target = QSize(self.img_label.size())
        card_ref = weakref.ref(self)
        result: dict[str, QImage] = {}

        def _load() -> None:
            try:
                if os.path.isfile(file):
                    result['image'] = loadImageFromPath(str(file), target)
                    touchCacheFile(file)
                    return
                image_bytes = requests.get(
                    self.folder.image_url, timeout=(3.05, 8)
                ).content
                os.makedirs(os.path.dirname(file), exist_ok=True)
                with open(file, 'wb') as f:
                    f.write(image_bytes)
                result['image'] = loadImageFromBytes(image_bytes, target)
            except (FileNotFoundError, PermissionError, requests.RequestException):
                return

        def _apply() -> None:
            card = card_ref()
            image = result.get('image')
            if card is not None and image is not None:
                _setImage(card.img_label, image)

        asyncTask(_load, (), self._ctx.main_window, _apply)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        return super().mousePressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = RoundMenu()
        rm_ac = QAction(SouthsideIcon.REMOVE.icon(), tr('folder_card.remove'))
        rm_ac.triggered.connect(
            lambda: event_bus.emit(Events.CLOUD_REMOVE_FOLDER, self)
        )
        rn_ac = QAction(SouthsideIcon.RENAME.icon(), tr('folder_card.rename'))
        rn_ac.triggered.connect(
            lambda: event_bus.emit(Events.CLOUD_RENAME_FOLDER, self)
        )
        add_local = QAction(SouthsideIcon.ADD.icon(), tr('folder_card.add_to_local'))
        add_local.triggered.connect(
            lambda: event_bus.emit(Events.CLOUD_ADD_TO_LOCAL, self)
        )
        menu.addActions([rm_ac, rn_ac, add_local])
        menu.exec(event.globalPos())


class SearchCloudFolderCard(QWidget):
    load: bool = False
    clicked = Signal()

    def __init__(self, folder: SearchCloudFolderInfo, width, ctx: AppContext):
        super().__init__()
        self.setFixedHeight(102)
        self.folder = folder
        self._ctx = ctx

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.img_label = QLabel()
        self.img_label.setFixedSize(100, 100)
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.img_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.img_label)

        right_layout = QVBoxLayout()

        right_layout.addSpacerItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        )

        title_label = SubtitleLabel(folder.folder_name)
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        title_label.setWordWrap(True)
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        right_layout.addWidget(title_label)

        author_label = QLabel(folder.author)
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        author_label.setWordWrap(True)
        author_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        right_layout.addWidget(author_label)

        right_layout.addSpacerItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        )

        layout.addLayout(right_layout)

    def loadDetailAndImage(self):
        if not self.folder.image_url:
            return
        hashed = hashlib.sha256(self.folder.image_url.encode()).hexdigest()
        file = os.path.join('data', 'cover', hashed)
        target = QSize(self.img_label.size())
        card_ref = weakref.ref(self)
        result: dict[str, QImage] = {}

        def _load() -> None:
            try:
                if os.path.isfile(file):
                    result['image'] = loadImageFromPath(str(file), target)
                    touchCacheFile(file)
                    return
                image_bytes = requests.get(
                    self.folder.image_url, timeout=(3.05, 8)
                ).content
                os.makedirs(os.path.dirname(file), exist_ok=True)
                with open(file, 'wb') as f:
                    f.write(image_bytes)
                result['image'] = loadImageFromBytes(image_bytes, target)
            except (FileNotFoundError, PermissionError, requests.RequestException):
                return

        def _apply() -> None:
            card = card_ref()
            image = result.get('image')
            if card is not None and image is not None:
                _setImage(card.img_label, image)

        asyncTask(_load, (), self._ctx.main_window, _apply)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            event_bus.emit(
                Events.VIEW_FOLDER,
                CloudFolderInfo(
                    folder_name=self.folder.folder_name,
                    image_url=self.folder.image_url,
                    id=self.folder.id,
                ),
            )
        return super().mousePressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = RoundMenu()
        rm_ac = QAction(SouthsideIcon.REMOVE.icon(), tr('folder_card.remove'))
        rm_ac.triggered.connect(
            lambda: event_bus.emit(Events.CLOUD_REMOVE_FOLDER, self)
        )
        rn_ac = QAction(SouthsideIcon.RENAME.icon(), tr('folder_card.rename'))
        rn_ac.triggered.connect(
            lambda: event_bus.emit(Events.CLOUD_RENAME_FOLDER, self)
        )
        add_local = QAction(SouthsideIcon.ADD.icon(), tr('folder_card.add_to_local'))
        add_local.triggered.connect(
            lambda: event_bus.emit(Events.CLOUD_ADD_TO_LOCAL, self)
        )
        menu.addActions([rm_ac, rn_ac, add_local])
        menu.exec(event.globalPos())
