"""Capture input widget for sticky TUI.

Handles Enter to capture thoughts via the service layer.
"""

from __future__ import annotations

from textual.widgets import Input


class CaptureInput(Input):
    """Capture input that handles Enter to capture thoughts.

    Posts a CaptureSubmitted message when the user presses Enter.
    """

    DEFAULT_CSS = """
    CaptureInput {
        dock: top;
        margin: 0 1;
        height: 3;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(
            placeholder="What's on your mind?",
            id="capture-input",
            **kwargs,
        )
