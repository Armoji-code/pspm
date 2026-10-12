from PySide6.QtCore import QByteArray
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import backup, saves
from .saves import _Job, side


def _arrow(x1, y1, x2, y2, both=False, dash=""):
    """A line with a head at (x2, y2), and at (x1, y1) too if `both` (QtSvg has no markers)."""
    def head(ax, ay, bx, by):
        d = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        ux, uy = (bx - ax) / d * 9, (by - ay) / d * 9
        return f'<polygon points="{bx},{by} {bx - ux - uy / 2},{by - uy + ux / 2} {bx - ux + uy / 2},{by - uy - ux / 2}"/>'
    line = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke-dasharray="{dash}"/>'
    return line + head(x1, y1, x2, y2) + (head(x2, y2, x1, y1) if both else "")


def save_map(c: dict, cloud: str, remote: str) -> str:
    """The save flow as SVG, in the theme's colors `c`: stick <-> PPSSPP sync, stick -> cloud, RESTORE back."""
    def box(x, y, w, title, sub):
        return (f'<rect x="{x}" y="{y}" width="{w}" height="56" rx="8" fill="none" stroke="{c["border"]}"/>'
                f'<text x="{x + w / 2}" y="{y + 24}" text-anchor="middle" fill="{c["display"]}">{title}</text>'
                f'<text x="{x + w / 2}" y="{y + 42}" text-anchor="middle">{sub}</text>')

    def lines(x, y, rows, anchor="start"):
        return "".join(f'<text x="{x}" y="{y + 14 * i}" text-anchor="{anchor}"{" fill=" + repr(c["text"]) if i == 0 else ""}>'
                       f"{t}</text>" for i, t in enumerate(rows))

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 410" font-family="Space Mono, monospace"
 font-size="10" fill="{c["text_secondary"]}">
{box(240, 10, 200, cloud, f"/{remote}/PSP/SAVEDATA")}
{box(20, 190, 200, "PSP MEMORY STICK", "PSP/SAVEDATA")}
{box(460, 190, 200, "PPSSPP ON THIS PC", "PPSSPP/SAVEDATA")}
{box(20, 350, 640, "SAFETY COPIES ON THIS PC", "ANYTHING ABOUT TO BE OVERWRITTEN IS MOVED HERE FIRST")}
<g stroke="{c["text_disabled"]}" fill="{c["text_disabled"]}">
{_arrow(130, 188, 270, 68)}{_arrow(300, 68, 160, 188)}{_arrow(222, 218, 458, 218, True)}
{_arrow(120, 248, 120, 348, dash="4 4")}{_arrow(560, 248, 560, 348, dash="4 4")}
</g>
{lines(20, 84, ["BACK UP", "ON PLUG-IN OR BACK UP NOW", "SYNCS PPSSPP FIRST", "NEVER DELETES IN THE CLOUD"])}
{lines(264, 118, ["RESTORE", "CLOUD → STICK", "BUTTON ONLY"])}
{lines(466, 96, ["NO DIRECT LINK CLOUD ↔ PPSSPP", "PPSSPP SAVES REACH THE CLOUD", "THROUGH THE STICK"])}
{lines(340, 210, ["SAVE SYNC"], "middle")}
{lines(340, 268, ["ON PLUG-IN, SYNC OR BACK UP NOW", "SIDE THAT CHANGED WINS", "BOTH CHANGED → ASKS YOU,",
                  "SHOWING BOTH DESCRIPTIONS"], "middle")}
</svg>'''


class BackupPage(QWidget):
    def __init__(self, cfg, state_file=backup.STATE, restore_backups=backup.RESTORE_BACKUPS,
                 saves_state=saves.STATE, save_backups=saves.BACKUPS):
        super().__init__()
        self.cfg, self.bcfg, self.dev, self.job = cfg, cfg["backup"], None, None
        self.state_file, self.restore_backups = state_file, restore_backups
        self.saves_state, self.save_backups = saves_state, save_backups
        self.map = QSvgWidget()
        self.map.setFixedSize(680, 410)
        self.state = backup.load_state(state_file)
        self.all = backup.providers()
        self.pick = QComboBox()
        for pid, cls in self.all.items():
            self.pick.addItem(cls.title or pid.upper(), pid)
        self.pick.setCurrentIndex(max(0, self.pick.findData(self.state.get("provider", self.bcfg["provider"]))))
        self.pick.currentIndexChanged.connect(self._switch)
        self.folder = QLineEdit(self.state.get("remote_folder", self.bcfg["remote_folder"]), placeholderText="CLOUD FOLDER")
        self.folder.editingFinished.connect(self._folder)
        self.what = QLabel()
        self.what.setObjectName("label")
        self.status = QLabel()
        self.status.setObjectName("label")
        self.sign = QPushButton()
        self.sign.clicked.connect(self.sign_in_out)
        top = QHBoxLayout()
        top.addWidget(self.pick)
        top.addWidget(self.sign)
        top.addWidget(self.folder, 1)
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        for text, fn in [("RESTORE", self.restore), ("BACK UP NOW", self.back_up)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        col = QVBoxLayout(self)
        col.addLayout(top)
        col.addWidget(self.what)
        col.addSpacing(16)
        col.addWidget(self.map)
        col.addStretch(1)
        col.addLayout(bar)
        self._picked()
        self.set_device(None)

    @property
    def pid(self):
        return self.pick.currentData()

    @property
    def remote(self):
        return self.folder.text().strip(" /") or self.bcfg["remote_folder"]

    def _pcfg(self):
        return self.bcfg.get(self.pid, {})

    def _done(self):
        # keyed by provider and folder: switching either re-uploads everything
        return self.state.setdefault("done", {}).setdefault(f"{self.pid}:{self.remote}", {})

    def _folder(self):
        self.state["remote_folder"] = self.remote
        backup.save_state(self.state, self.state_file)
        self._picked()

    def _switch(self, *_):
        self.state["provider"] = self.pid
        backup.save_state(self.state, self.state_file)
        self._picked()
        self.set_device(self.dev)

    def _picked(self):
        self.signed_in = bool(backup.token(self.pid))
        self.sign.setText("SIGN OUT" if self.signed_in else f"SIGN IN TO {self.pick.currentText()}")
        folders = ", ".join(self.cfg["device"]["folders"][f] for f in self.bcfg["folders"])
        self.what.setText(f"BACKS UP {folders} TO /{self.remote} ON PLUG-IN · RESTORE COPIES /{self.remote} BACK".upper())
        theme = self.cfg["themes"][self.cfg["app"]["theme"]]
        self.map.load(QByteArray(save_map(theme, self.pick.currentText(), self.remote).encode()))

    def _run(self, fn, then, msg):
        if self.job and self.job.isRunning():
            return
        self.status.setText(msg)
        self.job = _Job(fn, self)
        self.job.done.connect(then)
        self.job.failed.connect(lambda e: self.status.setText(f"[ERROR: {e}]"))
        self.job.start()

    def set_device(self, dev):
        self.dev = dev
        if dev and self.signed_in and self.bcfg["auto"]:
            self.back_up()
        else:
            self.status.setText("[NO PSP CONNECTED]" if not dev else "READY" if self.signed_in else "[SIGN IN TO BACK UP]")

    def sign_in_out(self):
        pid, pcfg = self.pid, self._pcfg()
        if self.signed_in:
            backup.set_token(pid, None)
            self._picked()
            self.status.setText("SIGNED OUT")
        elif not all(pcfg.get(k, "-") for k in ("client_id", "client_secret")):  # Dropbox has no secret
            self.status.setText(f"[{self.pick.currentText()} SIGN-IN ISN'T SET UP IN THIS BUILD · SEE README]")
        else:
            cls = self.all[pid]
            self._run(lambda: backup.set_token(pid, backup.login(cls, pcfg, self.bcfg)), self._signed_in,
                      "FINISH SIGNING IN IN YOUR BROWSER…")

    def _signed_in(self, _):
        self._picked()
        self.set_device(self.dev)

    def back_up(self):
        """Sync the stick with PPSSPP first (asking about saves changed on both sides), then upload."""
        if not (self.dev and self.signed_in):
            return self.set_device(self.dev)
        psp, pc = self.dev.path("saves"), saves.ppsspp_dir(self.cfg, saves.load_state(self.saves_state))
        if not pc:  # no PPSSPP on this PC: nothing to sync
            return self._upload([], None)

        def plan():
            with saves.LOCK:
                st = saves.load_state(self.saves_state)
                items = saves.plan(psp, pc, st["last"], st.setdefault("hashes", {}))
                saves.save_state(st, self.saves_state)
                return items

        self._run(plan, lambda items: self._upload(self._ask(items), (psp, pc)), "SYNCING PSP AND PPSSPP…")

    def _ask(self, items):
        todo = [i for i in items if i.action != saves.SAME]
        for it in (i for i in todo if i.conflict):
            box = QMessageBox(QMessageBox.Question, "PSPM", f"{it.name} CHANGED ON THE PSP AND IN PPSSPP. KEEP WHICH?\n\n"
                              f"PSP:  {side(it.psp_time, it.psp_desc)}\n\nPPSSPP:  {side(it.pc_time, it.pc_desc)}", parent=self)
            keep = {box.addButton("KEEP PSP", QMessageBox.AcceptRole): saves.TO_PC,
                    box.addButton("KEEP PPSSPP", QMessageBox.AcceptRole): saves.TO_PSP}
            box.addButton("SKIP", QMessageBox.RejectRole)
            box.setDefaultButton(next(b for b, a in keep.items() if a == it.action))  # newest side suggested
            box.exec()
            it.action = keep.get(box.clickedButton(), saves.SKIP)
        return todo

    def _upload(self, items, dirs):
        if not self.dev:  # unplugged while asking
            return self.set_device(None)
        pid, pcfg, cls, root, remote, done = self.pid, self._pcfg(), self.all[self.pid], self.dev.root, self.remote, self._done()
        folders = [self.dev.path(f) for f in self.bcfg["folders"]]

        def work():
            if items:
                with saves.LOCK:
                    st = saves.load_state(self.saves_state)
                    saves.apply(items, *dirs, st["last"], self.save_backups, st.setdefault("hashes", {}))
                    saves.save_state(st, self.saves_state)
            synced = sum(i.action in (saves.TO_PC, saves.TO_PSP) for i in items)
            files = backup.scan(root, folders)
            if not backup.pending(files, done):
                return synced, 0
            prov = cls(pcfg, backup.token(pid))
            return synced, backup.run(prov, files, done, remote, lambda: backup.save_state(self.state, self.state_file))

        def report(res):
            synced, n = res
            skipped = sum(i.action == saves.SKIP for i in items)
            self.status.setText(" · ".join(filter(None, [
                f"SYNCED {synced} SAVES" if synced else "", f"{skipped} SAVES NOT SYNCED" if skipped else "",
                f"BACKED UP {n} FILES" if n else "UP TO DATE"])))

        if dirs:
            self.job.wait()  # the sync check that led here may still be finishing
        self._run(work, report, "BACKING UP…")

    def restore(self):
        if not (self.dev and self.signed_in):
            return self.set_device(self.dev)
        pid, cls, root, remote, done = self.pid, self.all[self.pid], self.dev.root, self.remote, self._done()

        def work():
            n = backup.restore(cls(self._pcfg(), backup.token(pid)), root, remote, done, self.restore_backups)
            backup.save_state(self.state, self.state_file)
            return n

        self._run(work, lambda n: self.status.setText(
            f"RESTORED {n} FILES · REPLACED ONES ARE IN {self.restore_backups}" if n else "PSP ALREADY MATCHES THE CLOUD"),
            "RESTORING…")


class _Feature:
    id, title = "backup", "CLOUD BACKUP"

    def page(self, cfg):
        return BackupPage(cfg)


FEATURE = _Feature()
