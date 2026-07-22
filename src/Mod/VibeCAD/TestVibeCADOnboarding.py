# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD GUI lifecycle checks for the beginner first-launch dialog."""

from __future__ import annotations

import os
import tempfile
import unittest


class FirstLaunchOnboardingTest(unittest.TestCase):
    def test_dialog_has_intent_choices_and_privacy_status(self) -> None:
        from PySide import QtCore, QtWidgets
        import VibeCADGui as gui

        previous_home = os.environ.get("VIBECAD_HOME")
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
                    button for button in dialog.findChildren(QtWidgets.QPushButton)
                    if button.objectName().startswith("VibeCADStart_")
                ]
                self.assertEqual(len(choices), 7)
                self.assertTrue(all(button.accessibleName() for button in choices))
                self.assertTrue(all(button.focusPolicy() == QtCore.Qt.FocusPolicy.StrongFocus for button in choices))
                combined_style = " ".join(button.styleSheet().lower() for button in choices)
                self.assertNotIn("background", combined_style)
                self.assertNotIn("color:", combined_style)
                privacy = dialog.findChild(QtWidgets.QLabel, "VibeCADFirstLaunchPrivacy")
                self.assertIsNotNone(privacy)
                self.assertTrue(privacy.text().strip())
                dialog.close()
                dialog.deleteLater()
                gui._onboarding_dialog = None
        finally:
            if previous_home is None:
                os.environ.pop("VIBECAD_HOME", None)
            else:
                os.environ["VIBECAD_HOME"] = previous_home


def suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(FirstLaunchOnboardingTest)
