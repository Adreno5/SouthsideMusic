from typing import override

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import MessageBox, TextEdit, TransparentPushButton
from core.i18n import bindText, tr
from core.dialogs import SubtitleLabel


class ErrorPopupWindow(QDialog):
    def __init__(
        self,
        detail_text: str,
        parent: QWidget | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.detail_text = f'{title}\n\n{detail_text}' if title else detail_text
        self.setWindowTitle(title or tr('error_popup.oops_something_went_wrong'))
        global_layout = QVBoxLayout()
        title_label = SubtitleLabel()
        if title:
            title_label.setText(title)
        else:
            bindText(title_label, 'error_popup.oops_something_went_wrong')
        global_layout.addWidget(title_label)
        error_label = QLabel()
        bindText(error_label, 'error_popup.southside_music_encountered_some_errors')
        global_layout.addWidget(error_label)
        details_label = QLabel()
        bindText(details_label, 'error_popup.details')
        global_layout.addWidget(details_label)
        self.detail_content = TextEdit()
        self.detail_content.setReadOnly(True)
        self.detail_content.setPlainText(self.detail_text)
        global_layout.addWidget(self.detail_content)
        self.copy_btn = TransparentPushButton('')
        bindText(self.copy_btn, 'error_popup.copy_details')
        global_layout.addWidget(self.copy_btn)
        copy_label = QLabel()
        bindText(
            copy_label,
            'error_popup.copy_details_above_and_paste_it_to_the_issue_page_below',
        )
        global_layout.addWidget(copy_label)
        self.report_btn = TransparentPushButton('')
        bindText(self.report_btn, 'error_popup.report_this_problem')
        global_layout.addWidget(self.report_btn)
        self.setLayout(global_layout)

        self.setFixedSize(QApplication.primaryScreen().size() * 0.4)
        self.copy_btn.clicked.connect(self._copyDetails)
        self.report_btn.clicked.connect(self.report)

    def _copyDetails(self) -> None:
        QApplication.clipboard().setText(self.detail_text)

    def report(self) -> None:
        dialog = MessageBox(
            tr('error_popup.tip'),
            tr(
                'error_popup.describe_the_error_you_encountered_in_the_title_and_paste_the_details_'
            ),
            self,
        )
        dialog.cancelButton.hide()
        dialog.yesButton.setText('OK')
        dialog.exec()
        QDesktopServices.openUrl(
            QUrl(
                'https://github.com/Adreno5/SouthsideMusic/issues/new?title=describe%20the%20error'
            )
        )

    @override
    def exec(self) -> int:
        self.show()
        self.activateWindow()
        self.raise_()
        return super().exec()
