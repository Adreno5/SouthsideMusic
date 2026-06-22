from imports import (
    MessageBox,
    QDesktopServices,
    QDialog,
    QLabel,
    QUrl,
    QVBoxLayout,
    TextEdit,
    TransparentPushButton,
    QApplication,
    bindText,
    tr,
)
from core.dialogs import SubtitleLabel


class ErrorPopupWindow(QDialog):
    def __init__(self, detail_text: str):
        super().__init__()
        self.detail_text = detail_text

    def report(self):
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

    def exec(self) -> int:
        global_layout = QVBoxLayout()
        title_label = SubtitleLabel()
        bindText(title_label, 'error_popup.oops_something_went_wrong')
        global_layout.addWidget(title_label)
        error_label = QLabel()
        bindText(error_label, 'error_popup.southside_music_encountered_some_errors')
        global_layout.addWidget(error_label)
        details_label = QLabel()
        bindText(details_label, 'error_popup.details')
        global_layout.addWidget(details_label)
        detail_content = TextEdit()
        detail_content.setPlainText(self.detail_text)
        global_layout.addWidget(detail_content)
        copy_label = QLabel()
        bindText(
            copy_label,
            'error_popup.copy_details_above_and_paste_it_to_the_issue_page_below',
        )
        global_layout.addWidget(copy_label)
        self.report_btn = TransparentPushButton('')
        bindText(self.report_btn, 'error_popup.report_this_problem')
        self.report_btn.clicked.connect(self.report)
        global_layout.addWidget(self.report_btn)
        self.setLayout(global_layout)

        self.setFixedSize(QApplication.primaryScreen().size() * 0.4)

        self.show()
        return super().exec()
