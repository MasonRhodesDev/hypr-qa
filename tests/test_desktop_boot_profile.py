"""profiles/desktop-boot: the layout string, the vmkit flags `vm` passes, the install
hook's argument handling, and the full-boot report builder (no QEMU)."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(REPO, "profiles", "desktop-boot")
VM = os.path.join(PROFILE, "vm")
FULL_BOOT = os.path.join(REPO, "examples", "desktop-boot", "full-boot")

FAKE_VMKIT = """#!/bin/bash
echo "$VMKIT_LAYOUT|$*" >> "$FAKE_LOG"
exit 0
"""


class Profile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hqa-db-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.vmkit, self.log = os.path.join(self.tmp, "vmkit"), os.path.join(self.tmp, "calls")
        with open(self.vmkit, "w") as f:
            f.write(FAKE_VMKIT)
        os.chmod(self.vmkit, 0o755)
        self.env = dict(os.environ, VMKIT=self.vmkit, VMKIT_DIR=self.tmp, FAKE_LOG=self.log,
                        OVERLAY=os.path.join(self.tmp, "o.qcow2"), SEED=os.path.join(self.tmp, "seed.iso"))

    def vm(self, *a):
        return subprocess.run([VM, *a], env=self.env, capture_output=True, text=True, timeout=60)

    def test_layout_is_compositor_space_with_the_portrait_head_swapped(self):
        r = self.vm("layout")
        # head 0 centre ultrawide, head 1 left, head 2 right and rotated (3840x2160 -> 2160x3840)
        self.assertEqual(r.stdout.strip(), "1920,0,3440,1440;0,0,1920,1080;5360,0,2160,3840")

    def test_every_vmkit_call_carries_the_heads_uefi_and_layout(self):
        self.vm("heads")
        line = open(self.log).read().strip()
        layout, argv = line.split("|", 1)
        self.assertEqual(layout, "1920,0,3440,1440;0,0,1920,1080;5360,0,2160,3840")
        for want in ("--gpu-heads 3", "--gpu-res 3440x1440", "--uefi", "--ssh-user qa"):
            self.assertIn(want, argv)
        self.assertTrue(argv.endswith("heads"))

    def test_scripts_are_valid_bash(self):
        for name in ("build", "vm", "install-desktop-boot"):
            r = subprocess.run(["bash", "-n", os.path.join(PROFILE, name)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, name + r.stderr)
        r = subprocess.run(["bash", "-n", os.path.join(PROFILE, "files", "provision.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_install_hook_needs_something_to_install_and_refuses_both_vigil_forms(self):
        hook = os.path.join(PROFILE, "install-desktop-boot")
        r = subprocess.run([hook], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("nothing to install", r.stderr)
        r = subprocess.run([hook, "--vigil", "x", "--vigil-pkg", "y"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("exclusive", r.stderr)

    def test_install_hook_help_is_its_header(self):
        r = subprocess.run([os.path.join(PROFILE, "install-desktop-boot"), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--desktop-boot DIR", r.stdout)

    def test_the_profile_carries_no_host_specific_values(self):
        # portability: no home paths, hostnames or this machine's monitor names in committed files
        bad = ("/home/", "mason", "immaduck", "HDMI-A-1", "DP-1", "DP-2")
        for root, _, files in os.walk(PROFILE):
            for n in files:
                with open(os.path.join(root, n), errors="replace") as f:
                    text = f.read()
                for b in bad:
                    self.assertNotIn(b, text, f"{n} contains {b!r}")


@unittest.skipUnless(shutil.which("magick"), "needs ImageMagick")
class FullBootReport(unittest.TestCase):
    def test_report_has_a_row_per_moment_and_a_column_per_head(self):
        loader = importlib.machinery.SourceFileLoader("full_boot", FULL_BOOT)
        spec = importlib.util.spec_from_loader("full_boot", loader)
        fb = importlib.util.module_from_spec(spec)
        loader.exec_module(fb)
        out = tempfile.mkdtemp(prefix="hqa-fb-")
        self.addCleanup(shutil.rmtree, out)
        s = 1_000_000_000
        for h, (w, hh) in enumerate([(64, 32), (48, 32), (32, 48)]):
            d = os.path.join(out, "rec", f"head{h}", "frames")
            os.makedirs(d)
            rows = []
            for n in range(6):
                subprocess.run(["magick", "-size", f"{w}x{hh}", f"xc:gray{n * 10}", os.path.join(d, f"{n:06d}.png")], check=True)
                rows.append({"n": n, "t": 100 * s + n * s, "t_req": 100 * s + n * s, "inactive": h == 1})
            with open(os.path.join(out, "rec", f"head{h}", "frames.jsonl"), "w") as f:
                f.write("\n".join(json.dumps(r) for r in rows) + "\n")
        marks = [{"name": "power-on", "t": 100 * s}, {"name": "greeter", "t": 103 * s}]
        fb.report(out, 100 * s, marks, [0, 1, 2], every_s=2, width=32)
        with open(os.path.join(out, "report.html")) as f:
            page = f.read()
        self.assertEqual(page.count("<img "), 3 * (len(marks) + 4))     # 0,2,4 s rows + 2 marks + the 4 s tail row(s)
        self.assertIn("greeter", page)
        self.assertIn("inactive: no scanout", page)
        for h in (0, 1, 2):
            self.assertTrue(os.path.exists(os.path.join(out, "sheets", f"head{h}.png")))


if __name__ == "__main__":
    unittest.main()
