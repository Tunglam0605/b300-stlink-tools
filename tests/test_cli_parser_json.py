from __future__ import annotations

import unittest

from b300_cli.parser import parse_args


class CliJsonParserTests(unittest.TestCase):
    def test_json_defaults_false_when_not_requested(self) -> None:
        self.assertFalse(parse_args(["debug", "gateway", "--dry-run"]).json)

    def test_json_before_command_is_preserved(self) -> None:
        self.assertTrue(parse_args(["--json", "debug", "gateway", "--dry-run"]).json)

    def test_json_after_first_level_command_is_preserved(self) -> None:
        self.assertTrue(parse_args(["debug", "gateway", "--dry-run", "--json"]).json)

    def test_json_after_nested_subcommand_is_preserved(self) -> None:
        cases = (
            ["target", "inspect", "--json"],
            ["metadata", "show", "--json"],
            ["memory", "read", "0x08010000", "4", "--json"],
            ["update", "check", "--json"],
        )
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertTrue(parse_args(list(argv)).json)

    def test_json_between_parent_and_nested_subcommand_is_preserved(self) -> None:
        self.assertTrue(parse_args(["target", "--json", "inspect"]).json)


if __name__ == "__main__":
    unittest.main()
