from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class IsolatedUnittestRunnerTests(unittest.TestCase):
    def run_case(self, source: str, *runner_args: str):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sentinel_case.py").write_text(source, encoding="utf-8")
            result_file = root / "result.txt"
            env = dict(os.environ)
            env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
            env["B300_UNITTEST_RESULT_FILE"] = str(result_file)
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/run_unittest_module.py",
                    *runner_args,
                    "sentinel_case",
                ],
                cwd=Path(__file__).resolve().parents[1], env=env,
                capture_output=True, text=True,
            )
            verdict = result_file.read_text(encoding="ascii").strip() if result_file.exists() else None
            return result, verdict

    def test_pass_writes_pass_sentinel_and_zero_exit(self):
        result, verdict = self.run_case(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(verdict, "PASS")

    def test_failure_writes_fail_sentinel_and_nonzero_exit(self):
        result, verdict = self.run_case(
            "import unittest\nclass T(unittest.TestCase):\n    def test_bad(self): self.assertEqual(1, 2)\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(verdict, "FAIL")

    def test_split_cases_runs_every_test_in_fresh_child_process(self):
        result, verdict = self.run_case(
            "import os\nimport unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_one(self): os.environ['B300_SPLIT_CASE'] = 'set'\n"
            "    def test_two(self): self.assertNotIn('B300_SPLIT_CASE', os.environ)\n",
            "--split-cases",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(verdict, "PASS")

    def test_split_cases_returns_timeout_when_one_child_exceeds_its_budget(self):
        result, verdict = self.run_case(
            "import time\nimport unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_fast(self): self.assertTrue(True)\n"
            "    def test_slow(self): time.sleep(2)\n",
            "--split-cases",
            "--case-timeout",
            "1",
        )
        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        self.assertEqual(verdict, "FAIL")


if __name__ == "__main__":
    unittest.main()
