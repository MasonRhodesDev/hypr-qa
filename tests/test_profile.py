"""profiles/plain-hyprland/vm against a fake vmkit (no QEMU)."""
import os
import shutil
import socket
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VM = os.path.join(REPO, "profiles", "plain-hyprland", "vm")

FAKE_VMKIT = """#!/bin/bash
# log "VMKIT_STEP|command"; answer QMP with an empty HMP return
while [ $# -gt 0 ]; do case "$1" in --*) shift 2 ;; *) break ;; esac; done
echo "${VMKIT_STEP:-}|$1" >> "$FAKE_LOG"
[ "$1" = qmp ] && echo '{"return": ""}'
exit 0
"""


class Restore(unittest.TestCase):
    def test_restore_anchors_the_step_on_loadvm_not_the_hwclock_ssh(self):
        tmp = tempfile.mkdtemp(prefix="hqa-prof-")
        self.addCleanup(shutil.rmtree, tmp)
        vmkit, log = os.path.join(tmp, "vmkit"), os.path.join(tmp, "calls")
        with open(vmkit, "w") as f:
            f.write(FAKE_VMKIT)
        os.chmod(vmkit, 0o755)
        sock = socket.socket(socket.AF_UNIX)   # qmp_alive only needs a socket file there
        self.addCleanup(sock.close)
        sock.bind(os.path.join(tmp, "qmp.sock"))
        env = dict(os.environ, VMKIT=vmkit, VMKIT_DIR=tmp, OVERLAY=os.path.join(tmp, "o.qcow2"),
                   SEED=os.path.join(tmp, "seed.iso"), FAKE_LOG=log, VMKIT_STEP="restore-step")
        r = subprocess.run([VM, "restore", "snap"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = open(log).read().splitlines()
        self.assertIn("restore-step|qmp", calls)          # loadvm carries the step
        self.assertIn("|ssh", calls)                      # the hwclock resync does not
        self.assertNotIn("restore-step|ssh", calls)


if __name__ == "__main__":
    unittest.main()
