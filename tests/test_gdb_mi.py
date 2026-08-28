from __future__ import annotations

import queue
import subprocess
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from b300_core.gdb_mi import (
    GdbMiBackend,
    GdbMiCommandError,
    GdbMiProcessError,
    GdbMiTimeoutError,
)


class FakeStdin:
    def __init__(self) -> None:
        self.writes = []

    def write(self, value: str) -> None:
        self.writes.append(value)

    def flush(self) -> None:
        pass


class QueueStdout:
    _END = object()

    def __init__(self) -> None:
        self.lines = queue.Queue()

    def emit(self, line: str) -> None:
        self.lines.put(line)

    def close(self) -> None:
        self.lines.put(self._END)

    def __iter__(self):
        return self

    def __next__(self) -> str:
        line = self.lines.get()
        if line is self._END:
            raise StopIteration
        return line


class FakeGdbProcess:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.stdout = QueueStdout()
        self.terminated = False
        self.killed = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0
        self.stdout.close()

    def wait(self, timeout=None) -> int:
        return self.returncode if self.returncode is not None else 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.stdout.close()


class GdbMiBackendTests(unittest.TestCase):
    def make_backend(self, timeout: float = 0.2):
        process = FakeGdbProcess()
        backend = GdbMiBackend(
            process_factory=lambda command, **kwargs: process,
            response_timeout_seconds=timeout,
        )
        with mock.patch("b300_core.gdb_mi.resolve_gdb", return_value="test-gdb"):
            backend.start()
        return backend, process

    @staticmethod
    def invoke(operation):
        captured = []

        def run() -> None:
            try:
                captured.append(operation())
            except BaseException as error:
                captured.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        return thread, captured

    def test_connect_accepts_matching_connected_result(self) -> None:
        backend, process = self.make_backend()
        thread, captured = self.invoke(lambda: backend.connect("127.0.0.1", 3333))
        process.stdout.emit("1^connected\n")
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(captured[0].token, 1)
        self.assertEqual(captured[0].result_class, "connected")
        self.assertEqual(process.stdin.writes, ["1-target-select remote 127.0.0.1:3333\n"])
        backend.stop()

    def test_start_defers_gdb_resolution_and_uses_hidden_process_policy(self) -> None:
        process = FakeGdbProcess()
        captured = {}
        backend = GdbMiBackend(
            process_factory=lambda command, **kwargs: captured.update(kwargs) or process,
            response_timeout_seconds=0.2,
            platform_name="windows",
        )
        with mock.patch("b300_core.gdb_mi.resolve_gdb", return_value="bundled-gdb") as resolver:
            self.assertIsNone(backend.executable)
            backend.start()
        resolver.assert_called_once_with(None)
        self.assertTrue(captured["creationflags"] & subprocess.CREATE_NO_WINDOW)
        self.assertEqual(captured["stdin"], subprocess.PIPE)
        self.assertEqual(captured["stdout"], subprocess.PIPE)
        self.assertEqual(captured["stderr"], subprocess.STDOUT)
        self.assertTrue(captured["text"])
        self.assertFalse(captured["shell"])
        backend.stop()

    def test_wrong_token_and_async_records_do_not_satisfy_command(self) -> None:
        backend, process = self.make_backend()
        thread, captured = self.invoke(lambda: backend.connect("127.0.0.1", 3333))
        process.stdout.emit("*running,thread-id=\"all\"\n")
        process.stdout.emit("~\"console noise\\n\"\n")
        process.stdout.emit("2^connected\n")
        time.sleep(0.03)
        self.assertTrue(thread.is_alive())
        process.stdout.emit("1^connected\n")
        thread.join(timeout=1)

        self.assertEqual(captured[0].token, 1)
        self.assertEqual([record.prefix for record in backend.async_records], ["*", "~"])
        backend.stop()

    def test_error_result_raises_instead_of_reporting_connected(self) -> None:
        backend, process = self.make_backend()
        thread, captured = self.invoke(lambda: backend.connect("127.0.0.1", 3333))
        process.stdout.emit('1^error,msg="Remote communication error"\n')
        thread.join(timeout=1)

        self.assertIsInstance(captured[0], GdbMiCommandError)
        self.assertIn("Remote communication error", str(captured[0]))
        backend.stop()

    def test_tokens_increment_for_verified_control_commands(self) -> None:
        backend, process = self.make_backend()
        connect_thread, connect = self.invoke(lambda: backend.connect("127.0.0.1", 3333))
        process.stdout.emit("1^done\n")
        connect_thread.join(timeout=1)
        halt_thread, halt = self.invoke(backend.reset_halt)
        process.stdout.emit("2^done\n")
        halt_thread.join(timeout=1)

        self.assertEqual((connect[0].token, halt[0].token), (1, 2))
        self.assertEqual(process.stdin.writes[1], '2-interpreter-exec console "monitor reset halt"\n')
        backend.stop()

    def test_timeout_is_bounded_and_raises_meaningful_error(self) -> None:
        backend, _process = self.make_backend(timeout=0.03)
        with self.assertRaises(GdbMiTimeoutError):
            backend.connect("127.0.0.1", 3333)
        backend.stop()

    def test_unexpected_process_exit_raises_meaningful_error(self) -> None:
        backend, process = self.make_backend(timeout=0.2)
        process.returncode = 17
        with self.assertRaises(GdbMiProcessError):
            backend.connect("127.0.0.1", 3333)
        backend.stop()

    def test_load_symbols_quotes_path_and_accepts_done(self) -> None:
        backend, process = self.make_backend()
        with TemporaryDirectory() as directory:
            symbols = Path(directory) / "app with space.axf"
            symbols.write_text("symbols", encoding="utf-8")
            thread, captured = self.invoke(lambda: backend.load_symbols(symbols))
            process.stdout.emit("1^done\n")
            thread.join(timeout=1)

        self.assertEqual(captured[0].result_class, "done")
        self.assertIn(
            '"' + str(symbols.resolve()).replace("\\", "\\\\") + '"',
            process.stdin.writes[0],
        )
        backend.stop()

    def test_load_symbols_requires_elf_or_axf_file(self) -> None:
        backend, _process = self.make_backend()
        with TemporaryDirectory() as directory:
            invalid = Path(directory) / "application.hex"
            invalid.write_text("ignored", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ELF or AXF"):
                backend.load_symbols(invalid)
        backend.stop()

    def test_stop_sends_exit_and_reader_finishes(self) -> None:
        backend, process = self.make_backend()
        backend.stop()

        self.assertEqual(process.stdin.writes, ["1-gdb-exit\n"])
        self.assertTrue(process.terminated)
        self.assertFalse(backend.running)
        self.assertFalse(backend.reader_alive)


if __name__ == "__main__":
    unittest.main()
