import time

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from .. import diag, softmod
from .saves import _Job

ABOUT = """INSTALLS ARK CUSTOM FIRMWARE. PSPM ONLY COPIES THE OFFICIAL ARK FILES TO THE MEMORY STICK; YOU RUN THE INSTALLER ON THE PSP.
SUPPORTED: EVERY PSP-1000, 2000, 3000, GO AND STREET (E1000) ON OFFICIAL FIRMWARE 6.60 OR 6.61.
NOT SUPPORTED: OLDER OR NEWER FIRMWARE (UPDATE TO 6.61 FIRST), PS VITA, DEVELOPMENT UNITS.
ALREADY ON ARK-4? UPDATE FROM ARK'S OWN UPDATER INSTEAD. ON PRO OR ME WITH A PERMANENT CIPL, USE ARK'S DC10 GUIDE.
ARK IS LOST ON A FULL POWER-OFF UNTIL YOU ALSO INSTALL ITS CIPL ON THE PSP. PSPM COPIES ITS INSTALLER ONLY IF YOU TICK IT BELOW."""
CIPL_WARNING = """The cIPL installer makes ARK permanent by writing the PSP's flash when you run it.
If the PSP loses power while it runs, the PSP can be bricked and may only be saved with a Pandora battery.

Run it only after ARK works, with the battery at {min}% or more and the charger in, and don't touch the PSP until it's done.

Copy the cIPL installer too?"""


class SoftmodPage(QWidget):
    def __init__(self, cfg, backups=softmod.BACKUPS):
        super().__init__()
        self.cfg, self.dev, self.job, self.backups = cfg, None, None, backups
        s = cfg["softmod"]
        self.ark = QComboBox()
        for key in ("ark5", "ark4"):
            self.ark.addItem(f"{s[key]['name']} {s[key]['version']}".upper(), key)
        self.ark.setCurrentIndex(max(0, self.ark.findData(s["default"])))
        self.known = QLabel()
        self.known.setObjectName("label")
        self.fw = QCheckBox(f"MY PSP IS ON OFFICIAL FIRMWARE {' OR '.join(s['firmware'])}")
        self.battery = QCheckBox(f"BATTERY IS AT LEAST {s['min_battery']}% AND THE CHARGER IS IN")
        self.risk = QCheckBox("I UNDERSTAND CUSTOM FIRMWARE CAN BRICK A PSP AND COMES WITH NO WARRANTY")
        self.cipl = QCheckBox("ALSO COPY THE PERMANENT INSTALLER (CIPL) · IT WRITES THE PSP'S FLASH WHEN YOU RUN IT")
        self.status = QLabel()
        self.status.setObjectName("label")
        go = QPushButton("COPY ARK")
        go.clicked.connect(self.install)
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        bar.addWidget(go)
        col = QVBoxLayout(self)
        for w in (QLabel(ABOUT, wordWrap=True), self.ark, self.known, self.fw, self.battery, self.risk, self.cipl):
            col.addWidget(w)
        col.addStretch(1)
        col.addLayout(bar)
        self.set_device(None)

    def set_device(self, dev):
        self.dev = dev
        self.info = diag.read_info(dev, self.cfg) if dev else {}
        if self.job and self.job.isRunning():
            return
        self.fw.setVisible("firmware" not in self.info)
        if self.info:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.info["written"]))
            facts = [f"{k} {self.info[k]}" for k in ("model", "firmware", "battery", "charger") if k in self.info]
            self.known.setText(f"PSPM INFO, {when}: {' · '.join(facts)}".upper())
        else:
            self.known.setText("NO PSPM INFO REPORT (IT NEEDS CUSTOM FIRMWARE) · CONFIRM BELOW")
        self.status.setText("READY" if dev else "[NO PSP CONNECTED]")

    def _run(self, fn, then, msg):
        if self.job and self.job.isRunning():
            return
        self.status.setText(msg)
        self.job = _Job(fn, self)
        self.job.done.connect(then)
        self.job.failed.connect(lambda e: self.status.setText(f"[ERROR: {e}]".upper()))
        self.job.start()

    def install(self):
        if not self.dev:
            return
        if not all(b.isChecked() for b in (self.fw, self.battery, self.risk) if not b.isHidden()):
            self.status.setText("[TICK EVERY BOX ABOVE FIRST]")
            return
        dev, ark, cipl = self.dev, self.ark.currentData(), self.cipl.isChecked()
        if cipl and QMessageBox.warning(
            self, "Permanent install", CIPL_WARNING.format(min=self.cfg["softmod"]["min_battery"]),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            self.status.setText("CANCELLED")
            return

        def check():
            todo = softmod.files(self.cfg, ark, softmod.tool(self.cfg, ark), cipl)
            return todo, softmod.problems(dev, self.cfg, todo, self.info), softmod.existing(dev, self.cfg, ark, cipl)

        self._run(check, lambda r: self._confirm(dev, ark, cipl, *r), "CHECKING… (THE FIRST TIME ALSO DOWNLOADS ARK)")

    def _confirm(self, dev, ark, cipl, todo, problems, old):
        if problems:
            self.status.setText(f"[NOT COPIED: {'; '.join(problems)}]".upper())
            return
        a = self.cfg["softmod"][ark]
        folders = sorted(set(softmod.folders(self.cfg, ark, cipl).values()))
        text = (f"Copy {a['name']} {a['version']} ({len(todo)} files) to {dev.root}:\n  " + "\n  ".join(folders)
                + (f"\n\nAlready there, moved to a backup in {self.backups} first:\n  " + "\n  ".join(old)
                   if old else "")
                + "\n\nNothing is written to the PSP's flash now. Afterwards, on the PSP: leave USB mode, keep the "
                  f"charger in, and run {a['run']} from the Game menu."
                + (f" Only once ARK works, run {a['cipl_run']} to make it permanent." if cipl else ""))
        if QMessageBox.question(self, "Copy ARK", text) != QMessageBox.Yes:
            self.status.setText("CANCELLED")
            return
        self._run(lambda: softmod.install(dev, self.cfg, ark, todo, self.backups, cipl),
                  lambda kept: self._done(a, kept), "COPYING…")

    def _done(self, a, kept):
        backup = f" · OLD FILES IN {kept}" if kept else ""
        self.status.setText(f"COPIED AND VERIFIED · ON THE PSP, RUN {a['run']} FROM THE GAME MENU{backup}".upper())


class _Feature:
    id, title = "softmod", "SOFTMOD"

    def page(self, cfg):
        return SoftmodPage(cfg)


FEATURE = _Feature()
