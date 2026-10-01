"""Application entry point for the LyraSub Windows desktop workbench."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .constants import APP_DISPLAY_NAME
from .main_window import MainWindow

DARK_WORKBENCH_QSS = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}
QMainWindow, QWidget { background: #111722; color: #e7edf8; }
QToolBar { background: #151d2a; border: none; border-bottom: 1px solid #263349; spacing: 6px; padding: 5px 12px; }
QToolButton { color: #cbd6e8; padding: 6px 9px; border-radius: 4px; }
QToolButton:hover { background: #263650; color: #ffffff; }
QStatusBar { background: #101621; color: #94a7c3; border-top: 1px solid #243147; }
#topHeader { background: #0d1420; border-bottom: 1px solid #263349; }
#taskProgressPanel { background: #142536; border-bottom: 1px solid #32617c; }
#taskProgressTitle { color: #8bd8ee; font-weight: 700; min-width: 132px; }
#dangerButton { background: #663840; border-color: #995463; color: #ffe9ed; font-weight: 700; }
#dangerButton:hover { background: #824653; }
QProgressBar { border: 1px solid #35516d; border-radius: 5px; text-align: center; color: #eaf4ff; background: #101924; min-height: 18px; }
QProgressBar::chunk { background: #2d91b5; border-radius: 4px; }
#logoMark { background: #3a85a7; color: #f8fdff; border-radius: 18px; min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px; qproperty-alignment: AlignCenter; font-size: 20px; font-weight: 700; }
#appTitle { font-size: 19px; font-weight: 700; color: #ffffff; }
#appSubtitle, #mutedLabel, #pageDescription { color: #8fa1bc; }
#appSubtitle { font-size: 10px; font-weight: 600; letter-spacing: 1px; }
#projectTitle { color: #b9cae0; font-weight: 600; }
#navPanel { background: #151d2a; border-right: 1px solid #263349; }
#workflowNav { background: transparent; border: none; outline: none; color: #a9b9d0; }
#workflowNav::item { min-height: 36px; padding: 5px 9px; margin: 2px 0; border-radius: 5px; }
#workflowNav::item:selected { background: #23435e; color: #ffffff; font-weight: 700; }
#workflowNav::item:hover { background: #1d2c40; }
#sectionCaption { color: #51b6d6; font-size: 11px; font-weight: 700; letter-spacing: 1.2px; }
#pageTitle { font-size: 28px; font-weight: 700; color: #ffffff; }
#pageDescription { font-size: 14px; line-height: 1.4; }
#subheading { color: #dbe8fa; font-size: 14px; font-weight: 700; margin-top: 8px; }
QGroupBox { border: 1px solid #2b3950; border-radius: 7px; margin-top: 12px; padding: 16px 12px 12px; background: #172130; font-weight: 700; color: #dce8fa; }
QGroupBox::title { subcontrol-origin: margin; left: 11px; padding: 0 5px; color: #86bcd2; }
QLabel { background: transparent; }
#filePath { color: #c9daec; }
#infoCallout { background: #172a39; border: 1px solid #28516c; border-left: 3px solid #49b9da; border-radius: 6px; padding: 8px; color: #cfe9f4; }
#calloutTitle { color: #71cae6; font-weight: 700; }
#modelNote { background: #111a28; border: 1px solid #2a3a53; border-radius: 6px; padding: 10px; color: #91a8c5; }
#inspectorPanel { background: #151d2a; border-left: 1px solid #263349; }
#inspectorTitle { font-size: 16px; font-weight: 700; color: #f4f8ff; }
#audioPreviewPanel { background: #0d1622; border: 1px solid #2e536e; border-radius: 8px; }
#subtitlePreviewText { background: #07101a; border: 1px solid #27445c; border-left: 4px solid #48b6d7; border-radius: 6px; color: #f6fbff; font-size: 18px; font-weight: 700; padding: 10px 18px; }
QSlider::groove:horizontal { height: 6px; border-radius: 3px; background: #26384d; }
QSlider::sub-page:horizontal { border-radius: 3px; background: #2d91b5; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: #b9eafa; border: 1px solid #5fc4e3; }
QPushButton { background: #233149; color: #d8e5f6; border: 1px solid #334867; border-radius: 5px; padding: 7px 12px; min-height: 18px; }
QPushButton:hover { background: #304465; color: #ffffff; }
QPushButton:disabled { background: #1a2433; color: #61718b; border-color: #253146; }
#primaryButton { background: #2b82a5; border-color: #47a8ce; color: #ffffff; font-weight: 700; }
#primaryButton:hover { background: #349abe; }
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox { background: #0f1621; color: #e8eff9; border: 1px solid #344561; border-radius: 5px; padding: 6px; selection-background-color: #2e759b; }
QPlainTextEdit { selection-color: #ffffff; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #47adcf; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: #172130; color: #e8eff9; border: 1px solid #38506f; selection-background-color: #2d5f7f; }
QTableWidget { background: #111925; alternate-background-color: #172231; color: #e1eaf7; gridline-color: #2b3c55; border: 1px solid #30445f; border-radius: 6px; }
QTableWidget::item { padding: 7px; }
QTableWidget::item:selected { background: #234d6a; }
QHeaderView::section { background: #1c2a3e; color: #aebfd7; border: none; border-right: 1px solid #30445f; border-bottom: 1px solid #30445f; padding: 8px; font-weight: 700; }
QScrollBar:vertical { background: #111722; width: 11px; margin: 2px; }
QScrollBar::handle:vertical { background: #3a506e; min-height: 28px; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #52729b; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QCheckBox { color: #dce7f6; spacing: 7px; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #4a607d; border-radius: 3px; background: #101722; }
QCheckBox::indicator:checked { background: #2e91b5; border-color: #5cc0e2; }
"""


def _recognition_runtime_self_check() -> int:
    """Return a process status for installer verification without opening the UI."""
    try:
        import av  # type: ignore[import-not-found]  # noqa: F401
        import ctranslate2  # type: ignore[import-not-found]  # noqa: F401
        import numpy  # type: ignore[import-not-found]  # noqa: F401
        import onnxruntime  # type: ignore[import-not-found]  # noqa: F401
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]  # noqa: F401
    except Exception:
        return 2
    return 0


def main() -> int:
    if "--diagnose-recognition-runtime" in sys.argv:
        return _recognition_runtime_self_check()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)
    application = QApplication(sys.argv)
    application.setApplicationName(APP_DISPLAY_NAME)
    application.setOrganizationName("Lyra Studio")
    application.setFont(QFont("Microsoft YaHei UI", 10))
    application.setStyleSheet(DARK_WORKBENCH_QSS)
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
