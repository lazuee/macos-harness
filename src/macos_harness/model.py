"""Shared data shapes. Every module imports these. Nothing here imports AppKit."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import errors

API_VERSION = "MacosHarnessIPC-1"  # client and helper must agree on this
MAX_FRAME_BYTES = 8 * 1024 * 1024  # one message on the socket, either way
CALL_TIMEOUT_S = 120.0  # the client gives up after this long
# The system process that draws every app's open and save panels.
PANEL_SERVICE = "com.apple.appkit.xpc.openAndSavePanelService"

Point = tuple[float, float]  # global screen points, origin top left, signed
Rect = tuple[float, float, float, float]  # x, y, width, height in the same space


@dataclass(frozen=True)
class AppTarget:
    """One app the model named, after resolving."""

    # full path of the .app bundle. The session key, and the name used in error texts.
    path: str
    bundle_id: str
    name: str  # display name
    pid: int | None = None  # None while the app is not running


@dataclass(frozen=True)
class AppKind:
    electron: bool = False
    chromium: bool = False
    http: bool = False  # the bundle declares the http URL scheme, as every browser does
    catalyst: bool = False

    @property
    def web_like(self) -> bool:
        return self.electron or self.chromium or self.http


@dataclass(frozen=True)
class WindowRef:
    element: Any  # the window's accessibility element
    window_id: int  # window server id, 0 when unknown
    pid: int  # the process that owns the window. It can differ from the app's pid.
    title: str | None
    frame: Rect


@dataclass
class Line:
    """One printed element line, before it has a number."""

    depth: int
    text: str  # everything after "<id> ": role, state group, fields
    element: Any  # live accessibility element. Compare with ax.same(), never by hash.
    role: str  # raw accessibility role, used for pairing and refetch
    # printed after the text with one space. Ignored when two states are compared.
    detail: str = ""
    name: str = ""  # title, else description, used for refetch
    value: str = ""  # printed value. Refetch may ignore it.
    # Refetch key, filled by tree.py, compared by elements.py as one tuple:
    # (role, subrole, title, description, identifier, help, placeholder, url). The value
    # is compared apart.
    match: tuple[str, ...] = ()
    settable: bool = False
    # printed label -> accessibility action name
    actions: dict[str, str] = field(default_factory=dict)
    id: int | None = None  # set by the numbering in elements.py


@dataclass
class Capture:
    """What one state read saw, before numbering and diffing."""

    window_title: str  # header title. "" and "Unknown" are real values.
    app_name: str
    window_id: int
    # the main tree in depth-first order. Parent and child come from depth.
    lines: list[Line]
    # The focused element's line. Usually one of `lines` (same element, test with
    # ax.same). It may be an
    # element that is not in `lines`: elements.py then gives it a number after the main
    # tree.
    focused: Line | None = None
    selected_text: str | None = None
    # the selection block. Same rule as `focused`.
    selected: list[Line] = field(default_factory=list)
    menu_open: bool = False  # lines hold only the open menu
    loading: bool = False  # a loading sign was found. Settle reads this.
    # the window's page URL when the app is a browser. policy.check_url reads it.
    url: str | None = None


@dataclass(frozen=True)
class Shot:
    """One screenshot file and how its pixels map to the screen."""

    path: str
    scale: float  # screenshot pixels per screen point
    origin: Point  # top left of the captured rect in global points
    size: tuple[int, int]  # pixels

    def to_screen(self, x: float, y: float, at: Rect | None = None) -> Point:
        """Image pixels of this shot to global screen points.

        The one inverse of `to_image`, so the two can never drift apart. A window on
        a display left of the main one has a negative origin and maps correctly.

        `at` is the window's frame right now. The origin stored here is the one from
        the read, so a window that moved would otherwise put the click somewhere it
        never was. A window that was resized changes what a pixel means, so that
        refuses instead of aiming at the wrong place.
        """
        scale = self.valid_scale
        origin = self.origin
        if at is not None:
            x0, y0, width, height = at
            w, h = self.size[0] / scale, self.size[1] / scale
            if abs(w - width) > 1 or abs(h - height) > 1:  # a point: rounding only
                raise errors.screenshot_size(self.size)
            origin = (x0, y0)
        return (x / scale + origin[0], y / scale + origin[1])

    def to_image(self, x: float, y: float) -> Point:
        """Global screen points back to this shot's pixels."""
        scale = self.valid_scale
        return ((x - self.origin[0]) * scale, (y - self.origin[1]) * scale)

    @property
    def valid_scale(self) -> float:
        """The pixel-per-point scale, refusing a degenerate one.

        A zero or negative scale would otherwise map every point to the same place
        (or raise deep inside the division) instead of saying what went wrong.
        """
        if not self.scale > 0:
            raise errors.screenshot_scale(self.scale)
        return self.scale


@dataclass(frozen=True)
class Policy:
    decision: str  # "allowed", "denied" or "forbidden"
    risk: str  # "low" or "high"
    allow_persistent: bool
    warning: str | None  # shown in the approval prompt when risk is high
    ask: bool = True  # the person is asked before the app's first use


TYPE_DELAY = 0.00025  # seconds after every typed key event
SLOW_TYPING = {"com.apple.ScreenContinuity": 0.005}  # bundle id: its own delay


@dataclass
class AgentSession:
    """One calling agent. Found from MACOS_HARNESS_SESSION, else from the process that started the shell."""

    key: str
    ended: bool = False  # a turn-ended signal arrived and no turn-started since
    signals_turns: bool = False  # this host has sent turn-started at least once
    # Turn ends that also stop the action it still runs (Codex's interrupt, Pi's agent
    # end, a session's end). Claude Code's Stop is not one: background agents go on.
    cancels: int = 0
    exited: bool = False  # its host died and left this command's shell running
    # bundle ids whose instruction file was printed
    instructed: set[str] = field(default_factory=set)
    # app paths the person refused in this turn
    denied: set[str] = field(default_factory=set)
    # Paths of every .app bundle on the caller's parent chain (the terminal, the agent's
    # desktop app).
    # helper.py fills it when the session is made. policy.decide forbids these: an agent
    # may not drive its host.
    hosts: frozenset[str] = frozenset()
    # The MACOS_HARNESS_* variables of the call that is running now. helper.py replaces
    # it on every call.
    # tools.py reads MACOS_HARNESS_DIFF, actions.py reads
    # MACOS_HARNESS_ALWAYS_SIMULATE_CLICK (through app.agent.env).
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class AppSession:
    """One app as one agent sees it. Lives in the helper between calls."""

    agent: AgentSession
    target: AppTarget
    active: bool = False  # a state read succeeded since the session or the turn began
    needs_settle: bool = False  # an action ran since the last state read
    revisions: Any = None  # elements.Revisions: numbers, live elements, the last state
    shot: Shot | None = None  # the last screenshot, for x and y arguments
    # An action ran in a browser since its page was last checked against the blocked
    # list: the page may be another one now.
    page_unchecked: bool = False
    # True when the person used this app since the call began, or the client went
    # away (the host interrupted the tool). tools.py sets it for each call. actions.py
    # asks it before every event it posts, and inside a held click or key, to end the
    # hold early.
    cancel: Callable[[], bool] = lambda: False
    # The running app this session switched on (tree.enable, windows.watch,
    # revisions.watch). tools.py sets it at the first state read and switches the same
    # one off at turn end. None: nothing is switched on.
    watching: AppTarget | None = None
