from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import backup
from .saves import _Job


class BackupPage(QWidget):
    def __init__(self, cfg, state_file=backup.STATE):
        super().__init__()
        self.cfg, self.bcfg, self.dev, self.job = cfg, cfg["backup"], None, None
        self.state_file = state_file
        self.state = backup.load_state(state_file)
        self.all = backup.providers()
        self.pick = QComboBox()
        for pid, cls in self.all.items():
            self.pick.addItem(cls.title or pid.upper(), pid)
        self.pick.setCurrentIndex(max(0, self.pick.findData(self.state.get("provider", self.bcfg["provider"]))))
        self.pick.currentIndexChanged.connect(self._switch)
        self.what = QLabel()
        self.what.setObjectName("label")
        self.status = QLabel()
        self.status.setObjectName("label")
        self.sign = QPushButton()
        self.sign.clicked.connect(self.sign_in_out)
        now = QPushButton("BACK UP NOW")
        now.clicked.connect(self.back_up)
        top = QHBoxLayout()
        top.addWidget(self.pick)
        top.addWidget(self.sign)
        top.addStretch(1)
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        bar.addWidget(now)
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

    def _pcfg(self):
        return self.bcfg.get(self.pid, {})

    def _switch(self, *_):
        self.state["provider"] = self.pid
        backup.save_state(self.state, self.state_file)
        self._picked()
        self.set_device(self.dev)

    def _picked(self):
        self.signed_in = bool(backup.token(self.pid))
        self.sign.setText("SIGN OUT" if self.signed_in else "SIGN IN")
        folders = ", ".join(self.cfg["device"]["folders"][f] for f in self.bcfg["folders"])
        self.what.setText(f"BACKS UP {folders} TO /{self.bcfg['remote_folder']}".upper())

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
            self.status.setText(f"[SET backup.{pid}.client_id IN YOUR config.toml · SEE README]")
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
        pid, pcfg, cls, root = self.pid, self._pcfg(), self.all[self.pid], self.dev.root
        folders = [self.dev.path(f) for f in self.bcfg["folders"]]
        done = self.state.setdefault("done", {}).setdefault(pid, {})  # keyed by provider: switching re-uploads all

        def work():
            files = backup.scan(root, folders)
            if not backup.pending(files, done):
                return 0
            prov = cls(pcfg, backup.token(pid))
            return backup.run(prov, files, done, self.bcfg["remote_folder"],
                              lambda: backup.save_state(self.state, self.state_file))

        self._run(work, lambda n: self.status.setText(f"BACKED UP {n} FILES" if n else "UP TO DATE"), "BACKING UP…")


class _Feature:
    id, title = "backup", "CLOUD BACKUP"

    def page(self, cfg):
        return BackupPage(cfg)


FEATURE = _Feature()
