"""Terminal presentation shared by the demo, test-report and traffic tools.

Nothing here affects a decision. It exists so the tools read well on a screen
recording, on Windows as much as anywhere else, which takes three things the
standard library does not do by default:

* **Colour on Windows.** Classic Windows consoles ignore ANSI escape codes
  unless virtual-terminal processing is switched on for the handle, so it is
  switched on here. `NO_COLOR` and a redirected stream both turn colour off.
* **UTF-8 output.** A redirected Windows stream defaults to a legacy code page
  and raises on box-drawing characters. Output is reconfigured to UTF-8 with
  replacement, so a glyph the font lacks degrades to a placeholder, never to a
  traceback.
* **Quiet model loading.** sentence-transformers draws a progress bar for every
  embedding batch and huggingface_hub warns about anonymous downloads. Both are
  harmless and both would scroll the demo off the screen, so they are silenced
  for these tools only. Library code and its logging are left alone.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import textwrap
import warnings


def _enable_windows_vt() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001 - colour is cosmetic; never fail on it
        return False


def setup() -> None:
    """Call once at the top of a tool's main()."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    quiet_model_output()


def _model_cached(repo: str = "sentence-transformers/all-MiniLM-L6-v2") -> bool:
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface"), "hub")
    snapshots = os.path.join(hub, "models--" + repo.replace("/", "--"), "snapshots")
    return os.path.isdir(snapshots) and bool(os.listdir(snapshots))


def quiet_model_output() -> None:
    # Once the embedding model is on disk there is nothing to fetch, so stop
    # huggingface_hub checking for updates. That check is where the anonymous-
    # request warning comes from, and most of the startup delay. On a machine
    # that has never downloaded the model this is skipped and it downloads.
    if _model_cached():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    for name in ("sentence_transformers", "huggingface_hub", "transformers",
                 "chromadb", "httpx", "urllib3"):
        logging.getLogger(name).setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", category=ResourceWarning)
    warnings.filterwarnings("ignore", message=".*HF_TOKEN.*")
    warnings.filterwarnings("ignore", category=FutureWarning)


_COLOUR = (
    sys.stdout.isatty()
    and "NO_COLOR" not in os.environ
    and os.environ.get("TERM") != "dumb"
    and _enable_windows_vt()
)

_CODES = {
    "bold": "1", "dim": "2", "italic": "3",
    "red": "31", "green": "32", "yellow": "33", "blue": "34",
    "magenta": "35", "cyan": "36", "grey": "90",
    "bred": "91", "bgreen": "92", "byellow": "93", "bblue": "94", "bcyan": "96",
}


def style(text: str, *names: str) -> str:
    if not _COLOUR or not names:
        return text
    codes = ";".join(_CODES[n] for n in names if n in _CODES)
    return f"\033[{codes}m{text}\033[0m"


def visible_len(text: str) -> int:
    """Length on screen, ignoring colour codes."""
    out, i = 0, 0
    while i < len(text):
        if text[i] == "\033":
            j = text.find("m", i)
            i = j + 1 if j != -1 else len(text)
            continue
        out += 1
        i += 1
    return out


def width(cap: int = 100) -> int:
    return max(60, min(cap, shutil.get_terminal_size((100, 30)).columns - 2))


# Decision colours are the one convention every tool shares: green released,
# amber went to a person, red withheld.
ACTION_STYLE = {
    "answered": ("ANSWERED", "bgreen"),
    "escalated": ("ESCALATED", "byellow"),
    "blocked": ("BLOCKED", "bred"),
}


def action_badge(action: str) -> str:
    label, colour = ACTION_STYLE.get(action, (action.upper(), "bold"))
    return style(f" {label} ", "bold", colour) if _COLOUR else label


def rule(title: str = "", colour: str = "grey") -> str:
    w = width()
    if not title:
        return style("─" * w, colour)
    head = f"── {title} "
    return style(head, "bold", colour) + style("─" * max(0, w - visible_len(head)), colour)


def banner(title: str, subtitle: str = "") -> None:
    w = width()
    print()
    print(style("━" * w, "bcyan"))
    print(style(f"  {title}", "bold", "bcyan"))
    if subtitle:
        for line in textwrap.wrap(subtitle, w - 4):
            print(style(f"  {line}", "grey"))
    print(style("━" * w, "bcyan"))


class Panel:
    """A bordered box with labelled rows. Wraps long values to the width."""

    LABEL = 12

    def __init__(self, title: str, colour: str = "grey"):
        self.title, self.colour, self.lines = title, colour, []

    def row(self, label: str, value: str, *value_style: str, max_lines: int = 0) -> "Panel":
        inner = width() - 4 - self.LABEL - 1
        chunks = []
        for para in str(value).splitlines() or [""]:
            chunks.extend(textwrap.wrap(para, inner) or [""])
        if max_lines and len(chunks) > max_lines:
            hidden = len(chunks) - max_lines
            chunks = chunks[:max_lines] + [f"… {hidden} more line{'s' if hidden != 1 else ''}"]
        for i, chunk in enumerate(chunks):
            lab = style(f"{label:<{self.LABEL}}", "bold") if i == 0 else " " * self.LABEL
            self.lines.append(f"{lab} {style(chunk, *value_style) if value_style else chunk}")
        return self

    def text(self, value: str, *value_style: str) -> "Panel":
        inner = width() - 4
        for para in str(value).splitlines() or [""]:
            for chunk in textwrap.wrap(para, inner) or [""]:
                self.lines.append(style(chunk, *value_style) if value_style else chunk)
        return self

    def divider(self) -> "Panel":
        self.lines.append(None)
        return self

    def show(self) -> None:
        w = width()
        edge = lambda s: style(s, self.colour)
        head = f" {self.title} "
        print(edge("┌─") + style(head, "bold") + edge("─" * max(0, w - 3 - visible_len(head)) + "┐"))
        for line in self.lines:
            if line is None:
                print(edge("├" + "─" * (w - 2) + "┤"))
                continue
            pad = max(0, w - 4 - visible_len(line))
            print(edge("│ ") + line + " " * pad + edge(" │"))
        print(edge("└" + "─" * (w - 2) + "┘"))


def pause(enabled: bool, prompt: str = "Press Enter to continue") -> None:
    if not enabled:
        return
    try:
        input(style(f"\n  ▸ {prompt} ", "dim"))
    except EOFError:
        pass
