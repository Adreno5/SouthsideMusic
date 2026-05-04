from imports import (
    QDesktopServices,
    QDialog,
    QLabel,
    QMessageBox,
    QUrl,
    QVBoxLayout,
    TextEdit,
    TransparentPushButton,
    QApplication,
)
from utils.dialog_util import SubtitleLabel
from utils.icon_util import SouthsideIcon


class ErrorPopupWindow(QDialog):
    def __init__(self, detail_text: str):
        super().__init__()
        self.detail_text = detail_text

    def report(self):
        QMessageBox.information(self, 'tip', 'Describe the error you encountered in the title, and paste the details into the description')
        QDesktopServices.openUrl(
            QUrl("https://github.com/Adreno5/SouthsideMusic/issues/new?title=describe%20the%20error")
        )

    def exec(self) -> int:
        global_layout = QVBoxLayout()
        global_layout.addWidget(SubtitleLabel("Oops! Something went wrong"))
        global_layout.addWidget(QLabel("SouthsideMusic encountered some errors"))
        global_layout.addWidget(QLabel("Details:"))
        detail_content = TextEdit()
        detail_content.setPlainText(self.detail_text)
        global_layout.addWidget(detail_content)
        global_layout.addWidget(
            QLabel("Copy details above and paste it to the issue page below")
        )
        self.report_btn = TransparentPushButton("Report this Problem")
        self.report_btn.clicked.connect(self.report)
        global_layout.addWidget(self.report_btn)
        self.setLayout(global_layout)

        self.setFixedSize(QApplication.primaryScreen().size() * 0.4)

        self.show()
        return super().exec()
