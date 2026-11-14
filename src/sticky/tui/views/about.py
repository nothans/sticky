"""Static project information; no decorative animation or startup work."""

from textual.app import ComposeResult
from textual.widgets import Static

import sticky


class AboutView(Static):
    can_focus = True
    DEFAULT_CSS = """
    AboutView { height: 1fr; padding: 2 4; }
    #about-title { height: auto; text-style: bold; color: #9bbbd4; margin-bottom: 1; }
    #about-info { height: auto; max-width: 75; }
    """

    def compose(self) -> ComposeResult:
        yield Static(f"sticky {sticky.__version__}", id="about-title")
        yield Static(
            "Capture, find, edit, and recover your notes.\n\n"
            "Notes stay in a local SQLite database. Search combines local MiniLM embeddings with keyword matching. "
            "Capture and this interface do not send notes to a cloud model.\n\n"
            "Use the CLI to export notes or back up the database.\n\n"
            "github.com/nothans/sticky",
            id="about-info",
            markup=False,
        )
