# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD GUI lifecycle checks for the beginner first-launch dialog."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest


def _relative_luminance(color) -> float:
    channels = []
    for value in (color.redF(), color.greenF(), color.blueF()):
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first, second) -> float:
    high, low = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class FirstLaunchOnboardingTest(unittest.TestCase):
    def test_keyboard_choice_creates_a_usable_theme_safe_project(self) -> None:
        import FreeCAD as App
        from PySide import QtCore, QtGui, QtWidgets
        from PySide6 import QtTest
        import VibeCADGui as gui
        from VibeCADOnboarding import START_CHOICES, load_state

        previous_home = os.environ.get("VIBECAD_HOME")
        application = QtWidgets.QApplication.instance()
        original_palette = application.palette()
        prior_documents = set(App.listDocuments())
        existing = gui._onboarding_dialog
        if existing is not None:
            existing.close()
            existing.deleteLater()
            gui._onboarding_dialog = None
        try:
            with tempfile.TemporaryDirectory(prefix="vibecad-onboarding-") as directory:
                os.environ["VIBECAD_HOME"] = directory
                dialog = gui._show_first_launch_onboarding()
                self.assertIsNotNone(dialog)
                self.assertEqual(dialog.objectName(), "VibeCADFirstLaunch")
                choices = [
                    dialog.findChild(
                        QtWidgets.QPushButton,
                        f"VibeCADStart_{choice.choice_id}",
                    )
                    for choice in START_CHOICES
                ]
                self.assertEqual(len(choices), 7)
                self.assertTrue(all(button is not None for button in choices))
                self.assertTrue(all(button.accessibleName() for button in choices))
                self.assertTrue(
                    all(
                        button.focusPolicy() == QtCore.Qt.FocusPolicy.StrongFocus
                        for button in choices
                    )
                )
                combined_style = " ".join(button.styleSheet().lower() for button in choices)
                self.assertNotIn("background", combined_style)
                self.assertNotIn("color:", combined_style)
                privacy = dialog.findChild(QtWidgets.QLabel, "VibeCADFirstLaunchPrivacy")
                self.assertIsNotNone(privacy)
                self.assertTrue(privacy.text().strip())

                dialog.show()
                dialog.activateWindow()
                choices[0].setFocus(QtCore.Qt.FocusReason.TabFocusReason)
                QtWidgets.QApplication.processEvents()
                self.assertIs(QtWidgets.QApplication.focusWidget(), choices[0])
                QtTest.QTest.keyClick(choices[0], QtCore.Qt.Key_Tab)
                QtWidgets.QApplication.processEvents()
                self.assertIs(QtWidgets.QApplication.focusWidget(), choices[1])
                QtTest.QTest.keyClick(
                    choices[1],
                    QtCore.Qt.Key_Tab,
                    QtCore.Qt.KeyboardModifier.ShiftModifier,
                )
                QtWidgets.QApplication.processEvents()
                self.assertIs(QtWidgets.QApplication.focusWidget(), choices[0])

                for colors in (
                    {
                        "window": QtGui.QColor(248, 248, 248),
                        "text": QtGui.QColor(10, 10, 10),
                        "button": QtGui.QColor(238, 238, 238),
                        "button_text": QtGui.QColor(10, 10, 10),
                    },
                    {
                        "window": QtGui.QColor(28, 30, 34),
                        "text": QtGui.QColor(245, 245, 245),
                        "button": QtGui.QColor(45, 48, 54),
                        "button_text": QtGui.QColor(250, 250, 250),
                    },
                ):
                    palette = QtGui.QPalette(original_palette)
                    palette.setColor(QtGui.QPalette.ColorRole.Window, colors["window"])
                    palette.setColor(QtGui.QPalette.ColorRole.WindowText, colors["text"])
                    palette.setColor(QtGui.QPalette.ColorRole.Base, colors["window"])
                    palette.setColor(QtGui.QPalette.ColorRole.Text, colors["text"])
                    palette.setColor(QtGui.QPalette.ColorRole.Button, colors["button"])
                    palette.setColor(
                        QtGui.QPalette.ColorRole.ButtonText,
                        colors["button_text"],
                    )
                    application.setPalette(palette)
                    QtWidgets.QApplication.processEvents()
                    for button in choices:
                        current = button.palette()
                        ratio = _contrast_ratio(
                            current.color(QtGui.QPalette.ColorRole.ButtonText),
                            current.color(QtGui.QPalette.ColorRole.Button),
                        )
                        self.assertGreaterEqual(ratio, 4.5)
                        self.assertTrue(button.isEnabled())
                        self.assertEqual(
                            button.focusPolicy(), QtCore.Qt.FocusPolicy.StrongFocus
                        )

                choices[0].setFocus(QtCore.Qt.FocusReason.TabFocusReason)
                QtTest.QTest.keyClick(choices[0], QtCore.Qt.Key_Space)
                QtWidgets.QApplication.processEvents()

                self.assertEqual(
                    load_state(),
                    {
                        "schema": "vibecad-onboarding-v1",
                        "version": 1,
                        "completed": True,
                        "last_choice": "new-part",
                    },
                )
                document = App.ActiveDocument
                self.assertIsNotNone(document)
                file_path = Path(str(document.FileName)).resolve()
                self.assertTrue(file_path.is_file())
                self.assertEqual(file_path.parent.name, "local-projects")
                self.assertTrue(file_path.is_relative_to(Path(directory).resolve()))

                project = gui.get_service().project_context()
                self.assertTrue(project.get("persistent"))
                self.assertTrue(project.get("document_saved"))
                self.assertTrue(Path(str(project["manifest_path"])).is_file())
                dock = gui._find_dock()
                self.assertIsNotNone(dock)
                self.assertTrue(dock.isVisible())
                prompt = dock.findChild(QtWidgets.QPlainTextEdit, "VibePrompt")
                self.assertIsNotNone(prompt)
                self.assertTrue(prompt.isEnabled())
                self.assertFalse(prompt.isReadOnly())
                self.assertEqual(
                    prompt.toPlainText(),
                    "Create a new editable part for: ",
                )
                self.assertIsNone(gui._onboarding_dialog)
        finally:
            application.setPalette(original_palette)
            for name in set(App.listDocuments()) - prior_documents:
                App.closeDocument(name)
            if previous_home is None:
                os.environ.pop("VIBECAD_HOME", None)
            else:
                os.environ["VIBECAD_HOME"] = previous_home
            remaining = gui._onboarding_dialog
            if remaining is not None:
                remaining.close()
                remaining.deleteLater()
                gui._onboarding_dialog = None


def suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(FirstLaunchOnboardingTest)
