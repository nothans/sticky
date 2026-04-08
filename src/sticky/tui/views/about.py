"""About view with animated ASCII art logo and project info."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical, Center
from textual.reactive import reactive
from textual.widgets import Static

import sticky

LOGO = """
 ███████╗████████╗██╗ ██████╗██╗  ██╗██╗   ██╗
 ██╔════╝╚══██╔══╝██║██╔════╝██║ ██╔╝╚██╗ ██╔╝
 ███████╗   ██║   ██║██║     █████╔╝  ╚████╔╝
 ╚════██║   ██║   ██║██║     ██╔═██╗   ╚██╔╝
 ███████║   ██║   ██║╚██████╗██║  ██╗   ██║
 ╚══════╝   ╚═╝   ╚═╝ ╚═════╝╚═╝  ╚═╝   ╚═╝
"""

# Pad all lines to same width so text-align: center aligns them uniformly
_lines = LOGO.strip("\n").split("\n")
_w = max(len(l) for l in _lines)
LOGO = "\n" + "\n".join(l.ljust(_w) for l in _lines) + "\n"

TAGLINE = "your second brain — capture, organize, retrieve"

INFO_TEXT = """
[bold]Version[/]     {version}
[bold]Storage[/]     Local SQLite + sqlite-vec
[bold]Embeddings[/]  all-MiniLM-L6-v2 (local, 384d)
[bold]LLM[/]         OpenRouter (cloud, configurable)
[bold]Search[/]      Hybrid vector + FTS5 keyword

[dim]Capture thoughts with zero friction.
AI handles the organization.
Retrieve with semantic search.
Review with daily digests.[/]

[dim]github.com/sticky[/]
"""

# Colors to cycle through for the animated logo
LOGO_COLORS = [
    "#4ec9b0",  # teal
    "#61afef",  # blue
    "#c678dd",  # purple
    "#e06c75",  # coral
    "#d4a843",  # amber
    "#98c379",  # green
]


class AnimatedLogo(Static):
    """ASCII art logo that cycles through colors."""

    color_index: reactive[int] = reactive(0)

    DEFAULT_CSS = """
    AnimatedLogo {
        width: 1fr;
        height: auto;
        content-align: center middle;
        text-align: center;
        padding: 1 0;
    }
    """

    def on_mount(self) -> None:
        self.set_interval(2.0, self._cycle_color)

    def _cycle_color(self) -> None:
        self.color_index = (self.color_index + 1) % len(LOGO_COLORS)

    def watch_color_index(self, value: int) -> None:
        color = LOGO_COLORS[value]
        self.update(f"[bold {color}]{LOGO}[/]")

    def render(self) -> str:
        color = LOGO_COLORS[self.color_index]
        return f"[bold {color}]{LOGO}[/]"


class AboutView(Static):
    """About screen with animated logo and project info."""

    DEFAULT_CSS = """
    AboutView {
        height: 1fr;
        align: center middle;
    }

    #about-container {
        width: 60;
        height: auto;
        align: center middle;
    }

    #about-tagline {
        width: 1fr;
        text-align: center;
        color: $text-muted;
        text-style: italic;
        padding: 0 0 1 0;
    }

    #about-info {
        width: 1fr;
        padding: 1 4;
    }

    #about-separator {
        width: 1fr;
        text-align: center;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="about-container"):
                yield AnimatedLogo(id="about-logo")
                yield Static(f"[italic]{TAGLINE}[/]", id="about-tagline")
                yield Static("─" * 48, id="about-separator")
                yield Static(
                    INFO_TEXT.format(version=sticky.__version__),
                    id="about-info",
                )
