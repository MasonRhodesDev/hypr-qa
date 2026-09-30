import tomllib
import unittest

from hypr_qa import scenario as S
from hypr_qa import translate as X

SRC = """
name = "t"
profile = "p"

[[step]]
id = "open"
do = { vmkit = ["keys", "meta_l", "q"] }

  [[step.expect]]
  at = "+0ms..+1500ms"
  check = "changed"
  region = [22, 22, 0, 0]
  min_px = 1000

  [[step.expect]]
  at = "+300ms"
  anchor = "ack"
  check = "ocr"
  region = [0, 0, 0, 200]
  text = "hi"
  not = true

  [[step.expect]]
  at = "+0ms..+500ms"
  check = "pixel"
  region = [0, 0, 0, 4]
  color = "#00b4ff"
  not = true
  mode = "any"

[[step]]
id = "later"
do = { wait_ms = 10 }

  [[step.expect]]
  at = "-100ms..+100ms"
  check = "template"
  image = "templates/c.png"
  near = [640, 400]
  radius = 48
"""


class Translate(unittest.TestCase):
    def setUp(self):
        self.doc = S.validate(tomllib.loads(SRC))

    def test_shape(self):
        b = X.translate(self.doc)
        self.assertEqual([e["id"] for e in b], ["open#1", "open#2", "open#3", "later#1"])
        self.assertEqual(b[0], {"id": "open#1", "action": "open", "anchor": "send", "at": "+0ms..+1500ms",
                                "mode": "any", "check": "changed", "region": [22, 22, 0, 0], "min_px": 1000})
        self.assertEqual(b[1], {"id": "open#2", "action": "open", "anchor": "ack", "at": "+300ms", "mode": "all",
                                "not": True, "check": "ocr", "region": [0, 0, 0, 200], "text": "hi"})

    def test_mode_defaults(self):
        b = {e["id"]: e for e in X.translate(self.doc)}
        self.assertEqual(b["open#1"]["mode"], "any")      # window: any
        self.assertEqual(b["open#2"]["mode"], "all")      # not: never (schema v0.3)
        self.assertEqual(b["open#3"]["mode"], "any")      # explicit mode wins over the not default

    def test_not_only_when_true(self):
        b = X.translate(self.doc)
        self.assertNotIn("not", b[0])

    def test_template_resolved(self):
        b = X.translate(self.doc, resolve_asset=lambda p: "/abs/" + p)
        self.assertEqual(b[3]["image"], "/abs/templates/c.png")
        self.assertEqual((b[3]["near"], b[3]["radius"]), ([640, 400], 48))

    def test_performed_filter(self):
        b = X.translate(self.doc, performed={"open"})
        self.assertEqual({e["action"] for e in b}, {"open"})

    def test_ids_round_trip(self):
        self.assertEqual(X.split_id(X.expect_id("a-b", 3)), ("a-b", 3))


if __name__ == "__main__":
    unittest.main()
