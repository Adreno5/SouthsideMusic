from __future__ import annotations
from typing import TYPE_CHECKING

from imports import UPDATE_FM, Qt, event_bus
from imports import QPixmap
from imports import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentStyleSheet
from qframelesswindow import TitleBar

if TYPE_CHECKING:
    from views.song_card import SongCard


class SouthsideMusicTitleBar(TitleBar):
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.hBoxLayout.removeWidget(self.minBtn)
        self.hBoxLayout.removeWidget(self.maxBtn)
        self.hBoxLayout.removeWidget(self.closeBtn)

        self.titleLabel = CaptionLabel(self)
        self.hBoxLayout.insertWidget(
            0,
            self.titleLabel,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.titleLabel.setObjectName('titleLabel')
        self.window().windowTitleChanged.connect(self.setTitle)

        middle_layout = QHBoxLayout()
        middle_widget = QWidget()

        self.fm_label = QLabel(self)
        self.fm_label.setFixedSize(40, 40)
        self.fm_label.setObjectName('fm_label')
        self.hBoxLayout.insertWidget(
            1,
            self.fm_label,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        texts_layout = QVBoxLayout()

        self.song_title = QLabel(self)
        f = self.song_title.font()
        f.setPointSize(f.pointSize() + 1)
        self.song_title.setFont(f)
        self.song_title.setStyleSheet('font-weight: bold;')
        self.song_title.setObjectName('song_title')

        texts_layout.addWidget(self.song_title)

        self.lyric_label = QLabel(self)
        self.lyric_label.setObjectName('lyric_label')
        texts_layout.addWidget(self.lyric_label)

        middle_layout.addWidget(self.fm_label)
        middle_layout.addLayout(texts_layout)
        middle_widget.setLayout(middle_layout)

        self.hBoxLayout.addWidget(
            middle_widget, 2, alignment=Qt.AlignmentFlag.AlignVCenter
        )

        self.vBoxLayout = QVBoxLayout()
        self.buttonLayout = QHBoxLayout()
        self.buttonLayout.setSpacing(0)
        self.buttonLayout.setContentsMargins(0, 0, 0, 0)
        self.buttonLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.buttonLayout.addWidget(self.minBtn)
        self.buttonLayout.addWidget(self.maxBtn)
        self.buttonLayout.addWidget(self.closeBtn)
        self.vBoxLayout.addLayout(self.buttonLayout)
        self.vBoxLayout.addStretch(1)
        self.hBoxLayout.addLayout(self.vBoxLayout, 0)

        FluentStyleSheet.FLUENT_WINDOW.apply(self)

        event_bus.subscribe(UPDATE_FM, self.updateFM)

    def updateFM(self, pixmap: QPixmap, title: str):
        self.fm_label.show()
        self.song_title.show()
        self.fm_label.setPixmap(pixmap.scaled(self.fm_label.size()))
        self.song_title.setText(title)

    def setTitle(self, title):
        self.titleLabel.setText(title)
        self.titleLabel.adjustSize()

    def mousePressEvent(self, e):
        event_bus.enabled = False
        return super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        event_bus.enabled = True
        return super().mouseReleaseEvent(e)
