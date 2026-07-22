# Accessibility Review

## Automated and native evidence

The first-launch dialog gives every intent choice and the Not now control strong
keyboard focus. A native Qt test sends Tab and Shift-Tab events, activates a start
choice with the keyboard, and verifies that the result is a saved local project
with an enabled, editable message field.

The same native test applies controlled light and dark palettes. It verifies a
minimum 4.5:1 text-to-button contrast ratio for each first-launch choice and
confirms that each control stays enabled and keyboard focusable.

The candidate-review test uses the keyboard to activate Reject preview and Accept
revision. It also proves that Stop during review rejects the candidate and restores
the accepted state. The controls have explicit accessible names and strong focus.
The normal-run Stop and GUI steering paths have committed regression tests.

Current evidence:

- Focused Phase 2 Python set: 44 passed.
- Complete VibeCAD Python run reported by the Phase 2 implementation: 798 passed
  and 5 platform skips.
- `TestVibeCADOnboarding`: one test reported `OK` in the real FreeCAD GUI runtime.
- `TestVibeCADCandidateReview`: one test reported `OK` in the real FreeCAD GUI
  runtime.

Both native tests then reached the documented FreeCAD test-runner shutdown defect
and exited with code 134 after the successful unittest result. This is not a clean
process-exit claim.

## Manual review still required

Before production release, run a recorded review on a supported Mac with VoiceOver
enabled. Cover first launch, provider status, project creation, conversation,
activity-state announcements, Stop, candidate review, revision restore, save,
open, and export. Record:

- Spoken name, role, value, help, and state for each core control.
- Focus order and focus retention after a panel, dialog, or review state changes.
- Announcement of Understanding through Complete, Needs input, Failed safely, and
  Cancelled states without an excessive message rate.
- Keyboard-only access to menus, preferences, files, model navigation, important
  dimensions, warnings, revision history, and advanced-mode entry.
- Dark and light appearance with Increase Contrast and Reduce Transparency.
- Full Keyboard Access behavior and text editing with standard macOS keys.

The full VoiceOver review is open. Therefore, this document records an
accessibility baseline, not final accessibility acceptance.
