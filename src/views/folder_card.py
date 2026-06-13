import hashlib
import os

import requests

from core.app_context import AppContext
from core.downloader import asyncTask
from core.icons import SouthsideIcon
from core.models import (
    CloudFolderInfo,
    LocalFolderInfo,
    SongStorable,
    SearchCloudFolderInfo,
)
from imports import (
    CLOUD_ADD_TO_LOCAL,
    CLOUD_REMOVE_FOLDER,
    CLOUD_RENAME_FOLDER,
    IMAGE_ASSET_PERSISTED,
    LOCAL_ADD_TO_CLOUD,
    LOCAL_REMOVE_FOLDER,
    LOCAL_RENAME_FOLDER,
    VIEW_FOLDER,
    QAction,
    QContextMenuEvent,
    QHBoxLayout,
    QLabel,
    QMouseEvent,
    QPixmap,
    QSizePolicy,
    QSpacerItem,
    Qt,
    QVBoxLayout,
    QWidget,
    RoundMenu,
    Signal,
    event_bus,
)
from qfluentwidgets import SubtitleLabel


class LocalFolderCard(QWidget):
    clicked = Signal()

    def __init__(self, folder: LocalFolderInfo, width):
        super().__init__()
        self.setFixedSize(width, 52)
        self.folder = folder

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.img_label = QLabel()
        self.img_label.setFixedSize(50, 50)
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.img_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.img_label)

        title_label = QLabel(folder.folder_name)
        title_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        title_label.font().setPointSize(16)
        title_label.setWordWrap(True)
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(title_label)

        self._loadFirstSongImage()
        event_bus.subscribe(IMAGE_ASSET_PERSISTED, self._onImageAssetPersisted)

    def _onImageAssetPersisted(self, storable: SongStorable):
        songs = self.folder.songs
        if not songs:
            return
        if storable is not songs[0]:
            return
        self._loadFirstSongImage()

    def _loadFirstSongImage(self):
        songs = self.folder.songs
        if not songs:
            return
        first = songs[0]
        try:
            image_bytes = first.get_image_bytes()
            pixmap = QPixmap()
            pixmap.loadFromData(image_bytes)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.img_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.img_label.setPixmap(scaled)
        except FileNotFoundError:
            pass

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        return super().mousePressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = RoundMenu()
        rm_ac = QAction(SouthsideIcon.REMOVE.icon(), 'Remove')
        rm_ac.triggered.connect(lambda: event_bus.emit(LOCAL_REMOVE_FOLDER, self))
        rn_ac = QAction(SouthsideIcon.RENAME.icon(), 'Rename')
        rn_ac.triggered.connect(lambda: event_bus.emit(LOCAL_RENAME_FOLDER, self))
        addto_cloud = QAction(SouthsideIcon.ADD.icon(), 'Add to Cloud')
        addto_cloud.triggered.connect(lambda: event_bus.emit(LOCAL_ADD_TO_CLOUD, self))
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
        os.makedirs(os.path.join('data', 'cover'), exist_ok=True)
        hashed = hashlib.sha256(self.folder.image_url.encode()).hexdigest()
        file = os.path.join('data', 'cover', hashed)

        if not os.path.isfile(file):

            def _download():
                image_bytes = requests.get(self.folder.image_url).content

                def applyPixmap():
                    pixmap = QPixmap()
                    pixmap.loadFromData(image_bytes)
                    if not pixmap.isNull():
                        scaled = pixmap.scaled(
                            self.img_label.size(),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        self.img_label.setPixmap(scaled)

                    with open(file, 'wb') as f:
                        f.write(image_bytes)

                self._ctx.main_window.addScheduledTask(applyPixmap)

            asyncTask(_download, (), self._ctx.main_window)
        else:
            with open(file, 'rb') as f:
                image_bytes = f.read()
                pixmap = QPixmap()
                pixmap.loadFromData(image_bytes)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        self.img_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.img_label.setPixmap(scaled)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        return super().mousePressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = RoundMenu()
        rm_ac = QAction(SouthsideIcon.REMOVE.icon(), 'Remove')
        rm_ac.triggered.connect(lambda: event_bus.emit(CLOUD_REMOVE_FOLDER, self))
        rn_ac = QAction(SouthsideIcon.RENAME.icon(), 'Rename')
        rn_ac.triggered.connect(lambda: event_bus.emit(CLOUD_RENAME_FOLDER, self))
        add_local = QAction(SouthsideIcon.ADD.icon(), 'Add to Local')
        add_local.triggered.connect(lambda: event_bus.emit(CLOUD_ADD_TO_LOCAL, self))
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
        os.makedirs(os.path.join('data', 'cover'), exist_ok=True)
        hashed = hashlib.sha256(self.folder.image_url.encode()).hexdigest()
        file = os.path.join('data', 'cover', hashed)

        if not os.path.isfile(file):

            def _download():
                image_bytes = requests.get(self.folder.image_url).content

                def applyPixmap():
                    pixmap = QPixmap()
                    pixmap.loadFromData(image_bytes)
                    if not pixmap.isNull():
                        scaled = pixmap.scaled(
                            self.img_label.size(),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        self.img_label.setPixmap(scaled)

                    with open(file, 'wb') as f:
                        f.write(image_bytes)

                self._ctx.main_window.addScheduledTask(applyPixmap)

            asyncTask(_download, (), self._ctx.main_window)
        else:
            with open(file, 'rb') as f:
                image_bytes = f.read()
                pixmap = QPixmap()
                pixmap.loadFromData(image_bytes)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        self.img_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.img_label.setPixmap(scaled)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            event_bus.emit(
                VIEW_FOLDER,
                CloudFolderInfo(
                    folder_name=self.folder.folder_name,
                    image_url=self.folder.image_url,
                    id=self.folder.id,
                ),
            )
        return super().mousePressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = RoundMenu()
        rm_ac = QAction(SouthsideIcon.REMOVE.icon(), 'Remove')
        rm_ac.triggered.connect(lambda: event_bus.emit(CLOUD_REMOVE_FOLDER, self))
        rn_ac = QAction(SouthsideIcon.RENAME.icon(), 'Rename')
        rn_ac.triggered.connect(lambda: event_bus.emit(CLOUD_RENAME_FOLDER, self))
        add_local = QAction(SouthsideIcon.ADD.icon(), 'Add to Local')
        add_local.triggered.connect(lambda: event_bus.emit(CLOUD_ADD_TO_LOCAL, self))
        menu.addActions([rm_ac, rn_ac, add_local])
        menu.exec(event.globalPos())
