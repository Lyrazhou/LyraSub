"""Capture the default desktop workbench layout for local visual verification."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from lyrasub.app import DARK_WORKBENCH_QSS
from lyrasub.main_window import MainWindow


def main() -> None:
    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(DARK_WORKBENCH_QSS)
    window = MainWindow()
    window.resize(1600, 900)
    window.show()
    app.processEvents()
    output = Path(__file__).resolve().parents[1] / "docs" / "workbench-1600x900.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(output)):
        raise RuntimeError("未能保存工作台快照。")
    window.close()


if __name__ == "__main__":
    main()
