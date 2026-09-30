import os
import tempfile
import textwrap
import re
import tomllib
import unittest

from hypr_qa import scenario as S

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TOP = """
name = "t"
profile = "plain-hyprland"
"""
STEP = """
[[step]]
id = "s1"
do = { wait_ms = 100 }
"""


def doc(extra_top="", step_extra="", expect=None, tables=""):
    src = extra_top + TOP + tables + STEP + step_extra
    if expect is not None:
        src += "\n[[step.expect]]\n" + textwrap.dedent(expect)
    return S.validate(tomllib.loads(src))


class At(unittest.TestCase):
    def test_point_and_window(self):
        self.assertEqual(S.parse_at("+300ms"), ("point", 300_000_000))
        self.assertEqual(S.parse_at("-100ms"), ("point", -100_000_000))
        self.assertEqual(S.parse_at("+1.5s"), ("point", 1_500_000_000))
        self.assertEqual(S.parse_at("+50ms..+300ms"), ("window", 50_000_000, 300_000_000))
        self.assertEqual(S.parse_at("-100ms..+0ms"), ("window", -100_000_000, 0))

    def test_bad(self):
        for bad in ("300ms", "+300", "+300 ms", "+3min", "", "+50ms..", "..+5ms", "+1ms..+2ms..+3ms",
                    "+300ms..+50ms", "soon", 300, None):
            with self.subTest(bad=bad), self.assertRaises(S.ScenarioError):
                S.parse_at(bad)

    def test_bounds(self):
        self.assertEqual(S.at_bounds("+300ms"), (300_000_000, 300_000_000))
        self.assertEqual(S.at_bounds("-1s..+2s"), (-1_000_000_000, 2_000_000_000))

    def test_bad_at_error_names_the_expect(self):
        with self.assertRaisesRegex(S.ScenarioError, r"step\[1\] 's1' expect\[1\]: at: bad offset"):
            doc(expect='at = "300ms"\ncheck = "changed"\nregion = [0, 0, 10, 10]\n')


class Validate(unittest.TestCase):
    def test_examples_validate(self):
        for f in sorted(os.listdir(os.path.join(REPO, "examples"))):
            if f.endswith(".toml"):
                with self.subTest(f=f):
                    S.load(os.path.join(REPO, "examples", f))

    def test_minimal(self):
        d = doc()
        self.assertEqual(d["step"][0]["id"], "s1")

    def test_unknown_top_key(self):
        with self.assertRaisesRegex(S.ScenarioError, r"scenario: unknown key 'nmae'"):
            doc(extra_top='nmae = "x"\n')

    def test_unknown_step_key(self):
        with self.assertRaisesRegex(S.ScenarioError, r"step\[1\] 's1': unknown key 'setle_ms'"):
            doc(step_extra="setle_ms = 5\n")

    def test_unknown_expect_key(self):
        with self.assertRaisesRegex(S.ScenarioError, r"expect\[1\] \(pixel\): unknown key 'colour'"):
            doc(expect='at = "+0ms"\ncheck = "pixel"\nregion = [0,0,1,1]\ncolour = "#ffffff"\n')

    def test_field_of_another_check_is_unknown(self):
        with self.assertRaisesRegex(S.ScenarioError, r"unknown key 'min_px'"):
            doc(expect='at = "+0ms"\ncheck = "ocr"\ntext = "x"\nmin_px = 5\n')

    def test_unknown_guest_and_hyprhands_keys(self):
        with self.assertRaisesRegex(S.ScenarioError, r"\[guest\]: unknown key 'hyperland'"):
            doc(tables="[guest]\nhyperland = {}\n")
        with self.assertRaisesRegex(S.ScenarioError, r"\[hyprhands\]: unknown key 'args'"):
            doc(tables='[hyprhands]\nargv = ["x"]\nargs = []\n')

    def test_missing_required(self):
        with self.assertRaisesRegex(S.ScenarioError, r"missing required key 'profile'"):
            S.validate(tomllib.loads('name = "x"\n[[step]]\nid = "a"\ndo = { wait_ms = 1 }\n'))
        with self.assertRaisesRegex(S.ScenarioError, r"missing required key 'at'"):
            doc(expect='check = "changed"\nregion = [0,0,1,1]\n')

    def test_do_exactly_one(self):
        with self.assertRaisesRegex(S.ScenarioError, r"do must have exactly one"):
            S.validate(tomllib.loads('name="x"\nprofile="p"\n[[step]]\nid="a"\ndo={ wait_ms=1, session="x" }\n'))
        with self.assertRaisesRegex(S.ScenarioError, r"unknown action 'sleep'"):
            S.validate(tomllib.loads('name="x"\nprofile="p"\n[[step]]\nid="a"\ndo={ sleep=1 }\n'))

    def test_action_types(self):
        bad = ['{ vmkit = "keys ret" }', '{ vmkit = [] }', '{ hyprctl = "dispatch" }', '{ session = 3 }',
               '{ wait_ms = -1 }', '{ wait_ms = 1.5 }', '{ hyprhands = { x = 1 } }']
        for b in bad:
            with self.subTest(b=b), self.assertRaises(S.ScenarioError):
                S.validate(tomllib.loads(f'name="x"\nprofile="p"\n[hyprhands]\nargv=["s"]\n'
                                         f'[[step]]\nid="a"\ndo={b}\n'))

    def test_hyprhands_step_needs_table(self):
        with self.assertRaisesRegex(S.ScenarioError, r"needs a \[hyprhands\] table"):
            S.validate(tomllib.loads('name="x"\nprofile="p"\n[[step]]\nid="a"\ndo={ hyprhands={ op="state" } }\n'))

    def test_duplicate_and_bad_ids(self):
        with self.assertRaisesRegex(S.ScenarioError, r"duplicate step id 'a'"):
            S.validate(tomllib.loads('name="x"\nprofile="p"\n[[step]]\nid="a"\ndo={wait_ms=1}\n'
                                     '[[step]]\nid="a"\ndo={wait_ms=1}\n'))
        with self.assertRaisesRegex(S.ScenarioError, r"id must match"):
            S.validate(tomllib.loads('name="x"\nprofile="p"\n[[step]]\nid="a/b"\ndo={wait_ms=1}\n'))
        with self.assertRaisesRegex(S.ScenarioError, r"name must match"):
            S.validate(tomllib.loads('name="../x"\nprofile="p"\n[[step]]\nid="a"\ndo={wait_ms=1}\n'))

    def test_check_specific(self):
        cases = {
            'check = "pixel"\nregion = [0,0,1,1]\n': r"pixel needs color",
            'check = "pixel"\nregion = [0,0,1,1]\ncolor = "red"\n': r"color must be",
            'check = "pixel"\nregion = [0,0,1,1]\ncolor = "#ffffff"\ntolerance = 300\n': r"tolerance",
            'check = "pixel"\nregion = [0,0,1]\ncolor = "#ffffff"\n': r"region must be 4 integers",
            'check = "ocr"\n': r"exactly one of text or regex",
            'check = "ocr"\ntext = "a"\nregex = "b"\n': r"exactly one of text or regex",
            'check = "ocr"\nregex = "("\n': r"regex does not compile",
            'check = "template"\nimage = "t.png"\n': r"needs region, or near",
            'check = "template"\nimage = "t.png"\nnear = [1, 2]\n': r"radius",
            'check = "changed"\n': r"changed needs region",
            'check = "blink"\n': r"check must be one of",
            'check = "changed"\nregion = [0,0,1,1]\nanchor = "recv"\n': r"anchor must be",
            'check = "changed"\nregion = [0,0,1,1]\nmode = "some"\n': r"mode must be",
            'check = "changed"\nregion = [0,0,1,1]\nnot = "yes"\n': r"not must be",
        }
        for body, msg in cases.items():
            with self.subTest(body=body), self.assertRaisesRegex(S.ScenarioError, msg):
                doc(expect='at = "+0ms"\n' + body)

    def test_setup(self):
        d = S.validate(tomllib.loads('name="x"\nprofile="p"\n[[setup]]\npush={src="a",dst="/b",mode="0755"}\n'
                                     '[[setup]]\nsession="true"\n[[step]]\nid="a"\ndo={wait_ms=1}\n'))
        self.assertEqual(len(d["setup"]), 2)
        with self.assertRaisesRegex(S.ScenarioError, r"push.mode"):
            S.validate(tomllib.loads('name="x"\nprofile="p"\n[[setup]]\npush={src="a",dst="/b",mode="rwx"}\n'
                                     '[[step]]\nid="a"\ndo={wait_ms=1}\n'))
        with self.assertRaisesRegex(S.ScenarioError, r"setup\[1\]: must have exactly one"):
            S.validate(tomllib.loads('name="x"\nprofile="p"\n[[setup]]\nhyprctl=["x"]\n'
                                     '[[step]]\nid="a"\ndo={wait_ms=1}\n'))

    def test_load_reports_toml_errors_with_path(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write("name = \n")
        try:
            with self.assertRaisesRegex(S.ScenarioError, re.escape(f.name) + r": TOML"):
                S.load(f.name)
        finally:
            os.unlink(f.name)

    def test_asset_name(self):
        self.assertEqual(S.asset_name("templates/c.png"), "assets/templates/c.png")
        self.assertEqual(S.asset_name("../t/c.png"), "assets/_up/t/c.png")


if __name__ == "__main__":
    unittest.main()
