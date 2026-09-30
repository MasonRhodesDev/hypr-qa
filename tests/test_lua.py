import tomllib
import unittest

from hypr_qa.lua import LuaError, hl_config, lua_string, to_lua


class Lua(unittest.TestCase):
    def test_scalars(self):
        self.assertEqual(to_lua(True), "true")
        self.assertEqual(to_lua(False), "false")
        self.assertEqual(to_lua(3), "3")
        self.assertEqual(to_lua(-2), "-2")
        self.assertEqual(to_lua(0.5), "0.5")
        self.assertEqual(to_lua("foot"), '"foot"')

    def test_string_escapes(self):
        self.assertEqual(lua_string('a"b\\c\nd\te'), '"a\\"b\\\\c\\nd\\te"')
        self.assertEqual(lua_string("\x01"), '"\\001"')
        self.assertEqual(lua_string("a\x001"), '"a\\0001"')   # NUL then a digit: padded, not \01
        self.assertEqual(lua_string("ünï"), '"ünï"')

    def test_nested_table_from_toml(self):
        t = tomllib.loads('hyprland = { cursor = { no_hardware_cursors = true }, general = { gaps_in = 0 } }')
        self.assertEqual(hl_config(t["hyprland"]),
                         "hl.config({ cursor = { no_hardware_cursors = true }, general = { gaps_in = 0 } })")

    def test_keys_that_need_brackets(self):
        self.assertEqual(to_lua({"col.active_border": "0xff00ff00"}), '{ ["col.active_border"] = "0xff00ff00" }')
        self.assertEqual(to_lua({"end": 1}), '{ ["end"] = 1 }')
        self.assertEqual(to_lua({"9lives": 1}), '{ ["9lives"] = 1 }')
        self.assertEqual(to_lua({"a b": 1}), '{ ["a b"] = 1 }')

    def test_lists_and_empty(self):
        self.assertEqual(to_lua([1, "a", {"x": 2}]), '{ 1, "a", { x = 2 } }')
        self.assertEqual(to_lua({}), "{}")
        self.assertEqual(to_lua([]), "{}")

    def test_unsupported(self):
        t = tomllib.loads("d = 1979-05-27T07:32:00Z")
        with self.assertRaisesRegex(LuaError, r"hyprland.d: a datetime"):
            to_lua({"d": t["d"]})
        for v in (float("nan"), float("inf")):
            with self.assertRaises(LuaError):
                to_lua(v)
        with self.assertRaises(LuaError):
            hl_config([1])


if __name__ == "__main__":
    unittest.main()
