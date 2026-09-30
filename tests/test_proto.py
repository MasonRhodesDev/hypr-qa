import json
import os
import struct
import sys
import time
import unittest

from hypr_qa import proto

FAKE = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_hyprhands_serve.py")]


class Framing(unittest.TestCase):
    def test_encode_is_u32_be_then_json(self):
        wire = proto.encode({"op": "state"})
        (n,) = struct.unpack(">I", wire[:4])
        self.assertEqual(n, len(wire) - 4)
        self.assertEqual(json.loads(wire[4:]), {"op": "state"})

    def test_reply_with_blob_round_trips(self):
        r, w = os.pipe()
        os.write(w, proto.encode_reply({"ok": True}, b"PIX\x00\xff") + proto.encode_reply({"ok": False, "error": "e"}))
        os.close(w)
        dl = time.monotonic() + 5
        h, blob = proto.read_message(r, dl)
        self.assertEqual((h, blob), ({"ok": True, "blob": 5}, b"PIX\x00\xff"))
        h, blob = proto.read_message(r, dl)
        self.assertEqual((h, blob), ({"ok": False, "error": "e"}, None))
        with self.assertRaisesRegex(proto.ProtoError, "closed"):
            proto.read_message(r, dl)
        os.close(r)

    def test_short_read_times_out(self):
        r, w = os.pipe()
        os.write(w, struct.pack(">I", 10) + b"{")
        with self.assertRaisesRegex(proto.ProtoError, "timed out"):
            proto.read_message(r, time.monotonic() + 0.2)
        os.close(r); os.close(w)


class AgainstFakeServe(unittest.TestCase):
    def setUp(self):
        self.s = proto.Serve(FAKE, timeout=5)

    def tearDown(self):
        self.s.close()

    def test_requests_are_stamped_and_ordered(self):
        a = self.s.request({"op": "click", "x": 640, "y": 400})
        b = self.s.request({"op": "state"})
        self.assertTrue(a["ok"])
        self.assertEqual(a["reply"]["echo"], {"op": "click", "x": 640, "y": 400})
        self.assertLessEqual(a["t_send"], a["t_ack"])
        self.assertLess(a["t_ack"], b["t_send"])

    def test_ack_follows_the_reply(self):
        r = self.s.request({"op": "sleep", "ms": 120})
        self.assertTrue(r["ok"])
        self.assertGreaterEqual((r["t_ack"] - r["t_send"]) / 1e6, 115)

    def test_blob(self):
        r = self.s.request({"op": "capture", "size": 1000})
        self.assertTrue(r["ok"])
        self.assertEqual(r["reply"]["blob"], 1000)
        self.assertEqual(r["blob"], bytes(i % 256 for i in range(1000)))
        self.assertTrue(self.s.request({"op": "state"})["ok"])   # stream still in sync after the blob

    def test_error_reply_is_not_fatal(self):
        r = self.s.request({"op": "fail"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "fake failure")
        self.assertTrue(self.s.request({"op": "state"})["ok"])

    def test_dead_serve_fails_the_request_and_later_ones(self):
        r = self.s.request({"op": "exit"})
        self.assertFalse(r["ok"])
        self.assertIn("closed its output", r["error"])
        r2 = self.s.request({"op": "state"})
        self.assertFalse(r2["ok"])

    def test_garbage_reply(self):
        r = self.s.request({"op": "garbage"})
        self.assertFalse(r["ok"])
        self.assertIn("not JSON", r["error"])

    def test_timeout(self):
        r = self.s.request({"op": "sleep", "ms": 2000}, timeout=0.2)
        self.assertFalse(r["ok"])
        self.assertIn("timed out", r["error"])

    def test_clean_eof_exit(self):
        self.assertEqual(self.s.close(), 0)


if __name__ == "__main__":
    unittest.main()
