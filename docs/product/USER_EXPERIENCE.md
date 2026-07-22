# User Experience

## Primary user

The primary user knows the required object but might not know CAD terms. The application asks one question only when the answer changes function or geometry in an important way. It makes and reports safe assumptions for minor details.

## Main flow

1. The user starts a part, opens a file, or supplies a reference image.
2. The user states intent and known dimensions.
3. The application shows the states `Understanding`, `Inspecting design`, `Planning`, `Creating preview`, `Validating`, `Applying revision`, and `Complete`.
4. The user can stop, steer, accept, reject, undo, restore, or branch.
5. The result remains editable with named parameters and feature history.

The default workspace contains the viewport, a primary conversation surface, a compact model navigator, a property panel, and a revision timeline. Internal engine names and raw tool traces are in advanced mode only.

Selections are explicit context. Image dimensions are estimates until the user supplies a scale reference.
