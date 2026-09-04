"""Run one unittest module and exit before native GUI runtime teardown."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class HardExitTextResult(unittest.TextTestResult):
    """Report the module result, then exit before Qt/PySide native teardown."""

    def stopTestRun(self) -> None:
        successful = self.wasSuccessful()
        exit_code = 0 if successful else 1
        result_file = os.environ.get("B300_UNITTEST_RESULT_FILE", "").strip()
        if result_file:
            path = Path(result_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("PASS\n" if successful else "FAIL\n", encoding="ascii")

        self.printErrors()
        self.stream.writeln(self.separator2)
        self.stream.writeln("Ran %d tests" % self.testsRun)
        self.stream.writeln()
        if successful:
            status = "OK"
            if self.skipped:
                status += " (skipped=%d)" % len(self.skipped)
        else:
            parts = []
            if self.failures:
                parts.append("failures=%d" % len(self.failures))
            if self.errors:
                parts.append("errors=%d" % len(self.errors))
            if self.skipped:
                parts.append("skipped=%d" % len(self.skipped))
            status = "FAILED" + (" (%s)" % ", ".join(parts) if parts else "")
        self.stream.writeln(status)
        try:
            self.stream.flush()
        except Exception:
            pass
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code)


def iter_test_cases(suite: unittest.TestSuite):
    """Yield leaf test cases without retaining an enclosing Qt test suite."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_test_cases(item)
        else:
            yield item


def write_split_verdict(successful: bool) -> None:
    """Record one aggregate result instead of leaking an earlier child verdict."""
    result_file = os.environ.get("B300_UNITTEST_RESULT_FILE", "").strip()
    if not result_file:
        return
    path = Path(result_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("PASS\n" if successful else "FAIL\n", encoding="ascii")


def write_case_list(module: str, destination: Path) -> None:
    """Discover case ids in a disposable interpreter and hard-exit afterwards.

    Importing a PySide-backed test module can initialize native Qt state even before
    QApplication is created.  On Windows that state has proven capable of crashing
    the long-lived split-case parent after several otherwise passing children.  Case
    discovery therefore runs in its own process and exits with os._exit(), leaving
    the orchestrator process completely Qt-free.
    """
    suite = unittest.defaultTestLoader.loadTestsFromName(module)
    case_ids = [case.id() for case in iter_test_cases(suite)]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(case_ids) + ("\n" if case_ids else ""), encoding="utf-8")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if case_ids else 1)


def discover_case_ids(module: str, directory: Path) -> tuple[str, ...]:
    """Return case ids without importing the target test module in this process."""
    case_list_file = directory / "cases.txt"
    child_env = dict(os.environ)
    child_env.pop("B300_UNITTEST_RESULT_FILE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--list-cases-file",
            str(case_list_file),
            module,
        ],
        cwd=ROOT,
        env=child_env,
        timeout=30,
    )
    if result.returncode != 0 or not case_list_file.exists():
        return ()
    return tuple(
        line.strip()
        for line in case_list_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def run_split_cases(module: str, *, case_timeout: int | None = None) -> int:
    """Run every test case in a new interpreter to bound native Qt state."""
    with tempfile.TemporaryDirectory(prefix="b300-unittest-cases-") as directory:
        case_result_root = Path(directory)
        try:
            cases = discover_case_ids(module, case_result_root)
        except subprocess.TimeoutExpired:
            print("Timed out discovering tests in %s" % module, file=sys.stderr)
            write_split_verdict(False)
            return 124

        if not cases:
            print("No tests found in %s" % module, file=sys.stderr)
            write_split_verdict(False)
            return 1

        for index, case_id in enumerate(cases):
            print("=== %s ===" % case_id, flush=True)
            case_result_file = case_result_root / ("case-%04d.txt" % index)
            child_env = dict(os.environ)
            child_env["B300_UNITTEST_RESULT_FILE"] = str(case_result_file)
            try:
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), case_id],
                    cwd=ROOT,
                    env=child_env,
                    timeout=case_timeout,
                )
            except subprocess.TimeoutExpired:
                print(
                    "Timed out after %ss: %s" % (case_timeout, case_id),
                    file=sys.stderr,
                )
                write_split_verdict(False)
                return 124

            verdict = (
                case_result_file.read_text(encoding="ascii").strip()
                if case_result_file.exists()
                else ""
            )
            if result.returncode and verdict == "PASS":
                print(
                    "Accepted verified PASS for %s despite native teardown exit %s"
                    % (case_id, result.returncode),
                    file=sys.stderr,
                    flush=True,
                )
                continue
            if result.returncode:
                write_split_verdict(False)
                return result.returncode
            if verdict != "PASS":
                print(
                    "Child exited without a verified PASS sentinel: %s" % case_id,
                    file=sys.stderr,
                )
                write_split_verdict(False)
                return 1

    write_split_verdict(True)
    return 0


def run_and_exit(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one unittest module in an isolated process."
    )
    parser.add_argument(
        "--split-cases",
        action="store_true",
        help="run each discovered test case in its own child interpreter",
    )
    parser.add_argument(
        "--case-timeout",
        type=int,
        help="maximum seconds for each child test case (requires --split-cases)",
    )
    parser.add_argument(
        "--list-cases-file",
        type=Path,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("module", help="Dotted unittest module name, e.g. tests.test_gui_smoke")
    args = parser.parse_args(argv)

    if args.case_timeout is not None and args.case_timeout <= 0:
        parser.error("--case-timeout must be positive")
    if args.case_timeout is not None and not args.split_cases:
        parser.error("--case-timeout requires --split-cases")
    if args.list_cases_file is not None:
        if args.split_cases or args.case_timeout is not None:
            parser.error("--list-cases-file cannot be combined with split execution options")
        write_case_list(args.module, args.list_cases_file)
        raise RuntimeError("case discovery hard-exit returned unexpectedly")
    if args.split_cases:
        return run_split_cases(args.module, case_timeout=args.case_timeout)

    suite = unittest.defaultTestLoader.loadTestsFromName(args.module)
    pending = [suite]
    while pending:
        current = pending.pop()
        if isinstance(current, unittest.TestSuite):
            current._cleanup = False
            pending.extend(test for test in current if isinstance(test, unittest.TestSuite))

    unittest.TextTestRunner(verbosity=1, resultclass=HardExitTextResult).run(suite)
    raise RuntimeError("isolated unittest runner returned unexpectedly")


if __name__ == "__main__":
    sys.exit(run_and_exit())
