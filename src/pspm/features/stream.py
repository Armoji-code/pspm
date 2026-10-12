from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import cast, controls, deps, platform, stream
from ..ui.category import CategoryPicker
from ..ui.psp import SYMBOLS, PspView
from .saves import _Job

STEPS = """PSP TO PC
1. WITH THE PSP IN USB MODE, PRESS INSTALL PLUGIN.
2. LEAVE USB MODE AND RESTART THE PSP. KEEP THE CABLE IN.
3. PRESS START VIEWER. THE PSP'S SCREEN OPENS IN A WINDOW; YOUR KEYBOARD OR GAMEPAD CONTROLS THE PSP.

FIRST TIME: THE SETUP PAGE INSTALLS WHAT THE VIEWER NEEDS (WINE AND LIBUSB ON LINUX, THE USB DRIVER ON WINDOWS).
LINUX: THE FIRST START ASKS FOR YOUR PASSWORD ONCE TO ALLOW THE PSP'S USB."""

CAST_STEPS = """PC TO PSP
1. WITH THE PSP IN USB MODE, PRESS INSTALL PSP APP.
2. LEAVE USB MODE, KEEP THE CABLE IN, AND START PSPM SCREEN ON THE PSP.
3. PRESS START SENDING. YOUR SCREEN SHOWS ON THE PSP; ITS BUTTONS AND STICK CONTROL THE PC. HOME ON THE PSP QUITS.

REMOTEJOYLITE BLOCKS THIS WHILE IT'S ON FOR GAMES: REMOVE PLUGIN AND RESTART THE PSP FIRST.
LINUX: WAYLAND NEEDS GRIM (SWAY, HYPRLAND...; THE SETUP PAGE INSTALLS IT).
WINDOWS: BUTTONS CAN BE KEYS OR THE MOUSE; GAMEPAD BUTTONS WORK ON LINUX ONLY FOR NOW."""


def _name(target):
    return target.replace("_", " ").upper()


class StreamPage(QWidget):
    pad_state = Signal(int, int, int)  # from the sending thread: what the PSP's buttons and stick are doing

    def __init__(self, cfg):
        super().__init__()
        self.cfg, self.dev, self.job = cfg, None, None
        self.controls = controls.load(cfg)
        steps = QLabel(STEPS, wordWrap=True)
        self.status = QLabel()
        self.status.setObjectName("label")
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        for text, fn in [("INSTALL PLUGIN", self.install), ("REMOVE PLUGIN", self.remove),
                         ("START VIEWER", self.start)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.cast_job, self.sending = None, False
        self.cast_status = QLabel()
        self.cast_status.setObjectName("label")
        cast_bar = QHBoxLayout()
        cast_bar.addWidget(self.cast_status, 1)
        self.cat = CategoryPicker(cfg, "stream")
        cast_bar.addWidget(self.cat)
        for text, fn in [("INSTALL PSP APP", self.install_app), ("START SENDING", self.send),
                         ("STOP SENDING", self.stop_sending)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            cast_bar.addWidget(b)
        col = QVBoxLayout(self)
        col.addWidget(steps)
        col.addLayout(bar)
        col.addSpacing(24)
        col.addWidget(QLabel(CAST_STEPS, wordWrap=True))
        col.addLayout(cast_bar)
        self.psp = PspView(cfg["themes"][cfg["app"]["theme"]])
        self.psp.picked.connect(self.pick)
        self.pad_state.connect(self.psp.set_state)
        self.legend = QGridLayout()
        self.legend.setHorizontalSpacing(16)
        self.legend.setRowStretch(7, 1)  # rows at the top
        pad_row = QHBoxLayout()
        pad_row.addWidget(self.psp, 1)
        pad_row.addSpacing(24)
        pad_row.addLayout(self.legend)
        col.addLayout(pad_row, 1)
        self.show_controls()
        self.set_device(None)

    def set_device(self, dev):
        self.dev = dev
        self.cat.set_device(dev)
        if self.job and self.job.isRunning():
            return
        if not dev:
            self.status.setText("NO PSP CONNECTED · PLUGIN ALREADY INSTALLED? PRESS START VIEWER")
        else:
            self.status.setText("PLUGIN INSTALLED" if stream.installed(dev, self.cfg) else "PLUGIN NOT INSTALLED")
        if not (self.cast_job and self.cast_job.isRunning()):
            self.cast_status.setText("PSP IN USB MODE · " + ("PSP APP INSTALLED" if cast.installed(dev, self.cfg)
                                     else "PSP APP NOT INSTALLED") if dev else "NOT SENDING")

    def _run(self, fn, msg, then, failed=None):
        if self.job and self.job.isRunning():
            return
        self.status.setText(msg)
        self.job = _Job(fn, self)
        self.job.done.connect(lambda _: self.status.setText(then))
        self.job.failed.connect(failed or self._failed)
        self.job.start()

    def _failed(self, e):
        self.status.setText(f"[ERROR: {e}]".upper())

    def install(self):
        if self.dev:
            dev = self.dev
            self._run(lambda: stream.install(dev, self.cfg), "INSTALLING… (THE FIRST TIME ALSO DOWNLOADS REMOTEJOYLITE)",
                      "PLUGIN INSTALLED · LEAVE USB MODE, RESTART THE PSP, THEN START VIEWER")

    def remove(self):
        if self.dev:
            dev = self.dev
            self._run(lambda: stream.remove(dev, self.cfg), "REMOVING…", "PLUGIN REMOVED · RESTART THE PSP")

    def start(self, password=None):
        self._run(lambda: stream.start(self.cfg, password=password), "STARTING VIEWER…", "VIEWER STARTED",
                  self._start_failed)

    def _start_failed(self, e):
        if e == deps.NEED_PASSWORD:  # no polkit agent (SwayFX, i3...): ask here, allow the USB through sudo
            pw, ok = QInputDialog.getText(self, "Streaming", "Your password, once, to allow the PSP's USB (sudo):",
                                          QLineEdit.Password)
            if ok:
                self.job.wait()
                return self.start(pw)
            e = "cancelled"
        self._failed(e)


    def install_app(self):
        if self.dev and not (self.cast_job and self.cast_job.isRunning()):
            dev, cat = self.dev, self.cat.folder()
            self._cast(lambda: cast.install_app(dev, self.cfg, cat), "INSTALLING…",
                       lambda _: self.cast_status.setText("PSP APP INSTALLED · LEAVE USB MODE, START PSPM SCREEN"))

    def send(self, password=None):
        if self.cast_job and self.cast_job.isRunning():
            return
        self.sending = True
        self._cast(lambda: cast.send(self.cfg, lambda: not self.sending,
                                     lambda n, ms: self.cast_job.step.emit(f"SENDING · {n} FPS · CAPTURE {ms} MS"),
                                     password, self.controls, self.pad_state.emit),
                   "CONNECTING TO THE PSP…", lambda _: self.cast_status.setText("STOPPED"))

    def stop_sending(self):
        self.sending = False

    def _cast(self, fn, msg, then):
        self.cast_status.setText(msg)
        self.cast_job = job = _Job(fn, self)
        job.done.connect(then)
        job.step.connect(self.cast_status.setText)
        job.failed.connect(self._cast_failed)
        job.start()

    def _cast_failed(self, e):
        if e == deps.NEED_PASSWORD:  # same USB rule as START VIEWER, asked here when no desktop dialog can
            pw, ok = QInputDialog.getText(self, "Streaming", "Your password, once, to allow the PSP's USB (sudo):",
                                          QLineEdit.Password)
            if ok:
                self.cast_job.wait()
                return self.send(pw)
            e = "cancelled"
        self.cast_status.setText(f"[ERROR: {e}]".upper())


    def show_controls(self):
        if not self.legend.count():  # first call: one name + target label per control, in two columns
            for i, n in enumerate([*controls.BUTTONS, "stick"]):
                sym = SYMBOLS[n] if len(SYMBOLS[n]) == 1 and SYMBOLS[n] != n.upper() else ""
                name, target = QLabel(f"{sym} {n.upper()}".strip()), QLabel()
                name.setObjectName("label")
                target.setObjectName(f"target_{n}")
                self.legend.addWidget(name, i % 7, i // 7 * 2)
                self.legend.addWidget(target, i % 7, i // 7 * 2 + 1)
        for n in [*controls.BUTTONS, "stick"]:
            target = self.controls["stick"] if n == "stick" else self.controls["buttons"].get(n, "none")
            self.findChild(QLabel, f"target_{n}").setText(_name(target))

    def pick(self, name, where):
        """A menu of what the clicked PSP control can do; the pick is saved and used at once, even while sending."""
        menu = QMenu(self)
        if name == "stick":
            groups = {"": [f"stick:{m}" for m in controls.STICK]}
        else:
            pad = "GAMEPAD" + ("" if platform.name() == "linux" else " (LINUX ONLY)")
            keys = controls.KEYS
            groups = {"KEYBOARD: LETTERS": keys[:26], "KEYBOARD: NUMBERS": keys[26:36], "KEYBOARD: OTHER": keys[36:],
                      "MOUSE": controls.MOUSE, pad: controls.PAD, "": ["none"]}
        for title, targets in groups.items():
            sub = menu.addMenu(title) if title else menu
            for t in targets:
                a = QAction(_name(t.removeprefix("stick:")), sub)
                a.triggered.connect(lambda _=False, t=t: self.assign(name, t))
                sub.addAction(a)
        menu.exec(where)

    def assign(self, name, target):
        if target.startswith("stick:"):
            self.controls["stick"] = target.removeprefix("stick:")
        else:
            self.controls["buttons"][name] = target
        controls.save(self.controls)
        self.show_controls()


class _Feature:
    id, title = "stream", "STREAMING"

    def page(self, cfg):
        return StreamPage(cfg)


FEATURE = _Feature()
