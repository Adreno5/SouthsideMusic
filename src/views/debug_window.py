from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import QCompleter, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import LineEdit, SmoothScrollArea, TreeWidget

from utils import darkdetect_util as darkdetect


class DebugWindow(QWidget):
    def __init__(self, app, launchwindow=None) -> None:
        super().__init__()
        lw = launchwindow
        if lw:
            lw.top("Initializing debug window...")

        self._app = app
        if lw:
            lw.top("  allocating global layout")
        global_layout = QVBoxLayout()
        if lw:
            lw.top("  creating object name input")
        self.objname_inputer = LineEdit()
        global_layout.addWidget(self.objname_inputer)
        if lw:
            lw.top("  creating object label")
        self.obj_label = QLabel()
        global_layout.addWidget(self.obj_label)
        if lw:
            lw.top("  creating scroll area")
        scroll_widget = SmoothScrollArea()
        content_widget = QWidget()
        content_layout = QVBoxLayout()
        if lw:
            lw.top("  creating eval input")
        self.eval_inputer = LineEdit()
        self.eval_label = QLabel()
        content_layout.addWidget(self.eval_inputer)
        content_layout.addWidget(self.eval_label)
        if lw:
            lw.top("  creating property tree")
        self.tree = TreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Name", "Value"])
        self.tree.header().setStretchLastSection(False)
        content_layout.addWidget(self.tree)
        content_widget.setLayout(content_layout)
        scroll_widget.setWidget(content_widget)
        scroll_widget.setWidgetResizable(True)
        global_layout.addWidget(scroll_widget)
        self.selected_object: Optional[object] = None
        self.setLayout(global_layout)

        if lw:
            lw.top("  starting update timer")
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.updateDatas)
        self.update_timer.start(500)

        if lw:
            lw.top("  hiding debug window")
        self.hide()

    def updateDatas(self) -> None:
        if not self.isVisible():
            return
        if self.selected_object:
            self.tree.clear()

            def _recursive(obj: object, layer: int) -> list:
                res = []
                if layer > 5:
                    return [("To deep")]
                if hasattr(obj, "__dict__"):
                    for k, v in obj.__dict__.items():
                        if (
                            isinstance(v, int)
                            or isinstance(v, float)
                            or isinstance(v, str)
                            or isinstance(v, bool)
                            or isinstance(v, list)
                        ):
                            res.append((k, v))
                        else:
                            res.append((k, _recursive(v, layer + 1)))
                    return res
                else:
                    return []

            def _build_tree(data: list, parent):
                from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

                for item in data:
                    if isinstance(item, tuple):
                        key, value = item
                        if isinstance(value, list):
                            tree_item = QTreeWidgetItem([key, ""])
                            if isinstance(parent, QTreeWidget):
                                parent.addTopLevelItem(tree_item)
                            else:
                                parent.addChild(tree_item)
                            _build_tree(value, tree_item)
                        else:
                            tree_item = QTreeWidgetItem([key, str(value)])
                            if isinstance(parent, QTreeWidget):
                                parent.addTopLevelItem(tree_item)
                            else:
                                parent.addChild(tree_item)
                    elif isinstance(item, list):
                        for sub_item in item:
                            _build_tree([sub_item], parent)

            result = _recursive(self.selected_object, 1)
            _build_tree(result, self.tree)

            self.tree.expandAll()

            self.obj_label.setText(str(self.selected_object))
        self.tree.setColumnWidth(0, self.width() // 2 - 15)
        self.tree.setColumnWidth(1, self.width() // 2 - 15)

        self.selected_object = globals().get(self.objname_inputer.text())

        completer = QCompleter(list(globals().keys()), self.objname_inputer)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setMaxVisibleItems(20)
        self.objname_inputer.setCompleter(completer)

        try:
            self.eval_label.setText(str(eval(self.eval_inputer.text())))
        except:
            pass

        self.setStyleSheet(
            f"background: {'white' if darkdetect.isLight() else 'black'}"
        )

    def closeEvent(self, event: QCloseEvent):
        event.ignore()
        self.hide()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            return super().keyPressEvent(event)
