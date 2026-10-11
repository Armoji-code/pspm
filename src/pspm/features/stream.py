from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import stream
from .saves import _Job

STEPS = """1. WITH THE PSP IN USB MODE, PRESS INSTALL PLUGIN.
2. LEAVE USB MODE AND RESTART THE PSP. KEEP THE CABLE IN.
3. PRESS START VIEWER. THE PSP'S SCREEN OPENS IN A WINDOW; YOUR KEYBOARD OR GAMEPAD CONTROLS THE PSP.

WINDOWS, FIRST TIME: WITH THE PLUGIN RUNNING, USE ZADIG (ZADIG.AKEO.IE) TO INSTALL LIBUSB-WIN32 FOR "PSP TYPE B".
LINUX: NEEDS WINE AND LIBUSB 0.1; THE FIRST START ASKS FOR YOUR PASSWORD ONCE TO ALLOW THE PSP'S USB.
PC TO PSP ISN'T AVAILABLE: NO MAINTAINED TOOL FOR IT EXISTS."""


class StreamPage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg, self.dev, self.job = cfg, None, None
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
        col = QVBoxLayout(self)
        col.addWidget(steps)
        col.addStretch(1)
        col.addLayout(bar)
        self.set_device(None)

    def set_device(self, dev):
        self.dev = dev
        if self.job and self.job.isRunning():
            return
        if not dev:
            self.status.setText("NO PSP CONNECTED · PLUGIN ALREADY INSTALLED? PRESS START VIEWER")
        else:
            self.status.setText("PLUGIN INSTALLED" if stream.installed(dev, self.cfg) else "PLUGIN NOT INSTALLED")

    def _run(self, fn, msg, then):
        if self.job and self.job.isRunning():
            return
        self.status.setText(msg)
        self.job = _Job(fn, self)
        self.job.done.connect(lambda _: self.status.setText(then))
        self.job.failed.connect(lambda e: self.status.setText(f"[ERROR: {e}]".upper()))
        self.job.start()

    def install(self):
        if self.dev:
            dev = self.dev
            self._run(lambda: stream.install(dev, self.cfg), "INSTALLING… (THE FIRST TIME ALSO DOWNLOADS REMOTEJOYLITE)",
                      "PLUGIN INSTALLED · LEAVE USB MODE, RESTART THE PSP, THEN START VIEWER")

    def remove(self):
        if self.dev:
            dev = self.dev
            self._run(lambda: stream.remove(dev, self.cfg), "REMOVING…", "PLUGIN REMOVED · RESTART THE PSP")

    def start(self):
        self._run(lambda: stream.start(self.cfg), "STARTING VIEWER…", "VIEWER STARTED")


class _Feature:
    id, title = "stream", "STREAMING"

    def page(self, cfg):
        return StreamPage(cfg)


FEATURE = _Feature()
