from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .. import backup
from .saves import _Job


class BackupPage(QWidget):
    def __init__(self, cfg, state_file=backup.STATE, restore_backups=backup.RESTORE_BACKUPS):
        super().__init__()
        self.cfg, self.bcfg, self.dev, self.job = cfg, cfg["backup"], None, None
        self.state_file, self.restore_backups = state_file, restore_backups
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
        elif not pcfg.get("client_id"):
            self.status.setText(f"[{self.pick.currentText()} SIGN-IN ISN'T SET UP IN THIS BUILD · SEE README]")
        else:
            cls = self.all[pid]
            self._run(lambda: backup.set_token(pid, backup.login(cls, pcfg, self.bcfg)), self._signed_in,
                      "FINISH SIGNING IN IN YOUR BROWSER…")

    def _signed_in(self, _):
        self._picked()
        self.set_device(self.dev)

    def back_up(self):
        if not (self.dev and self.signed_in):
            return self.set_device(self.dev)
        pid, pcfg, cls, root, remote, done = self.pid, self._pcfg(), self.all[self.pid], self.dev.root, self.remote, self._done()
        folders = [self.dev.path(f) for f in self.bcfg["folders"]]

        def work():
            files = backup.scan(root, folders)
            if not backup.pending(files, done):
                return 0
            prov = cls(pcfg, backup.token(pid))
            return backup.run(prov, files, done, remote, lambda: backup.save_state(self.state, self.state_file))

        self._run(work, lambda n: self.status.setText(f"BACKED UP {n} FILES" if n else "UP TO DATE"), "BACKING UP…")

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
