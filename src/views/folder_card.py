from core.models import FolderInfo, SongStorable
from imports import FlowLayout, QHBoxLayout, QLabel, QMouseEvent, QPixmap, Qt, QVBoxLayout, QWidget, Signal
from qfluentwidgets import SubtitleLabel


class FolderCard(QWidget):
    clicked = Signal()

    def __init__(self, folder: FolderInfo, width):
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

        title_label = QLabel(folder['folder_name'])
        title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        title_label.font().setPointSize(16)
        title_label.setWordWrap(True)
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(title_label)

        self._loadFirstSongImage()

    def _loadFirstSongImage(self):
        songs = self.folder['songs']
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
        self.clicked.emit()
        return super().mousePressEvent(event)
