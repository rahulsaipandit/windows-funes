"""Tests for the sessions page's backend: the swap (all or nothing) and the parsers of funes's output.

    python -m unittest discover -s tools/sessions-ui -v
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402

import argparse  # noqa: E402

ITEMS = ["memory", "state.json", "index-coverage.json"]
_real_rename = Path.rename
server.ARGS = argparse.Namespace(funes=None, port=0, source=None, harness="claude")


def write(root, files):
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def tree(root):
    return {str(p.relative_to(root)).replace("\\", "/"): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


class SwapItems(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.home, self.new = base / "home", base / "home-pruned"
        write(self.home, {"memory/data": "OLD", "state.json": "old-state", "index-coverage.json": "old-cov", "agents/keep": "x"})
        write(self.new, {"memory/data": "NEW", "state.json": "new-state", "index-coverage.json": "new-cov"})
        self.before_home, self.before_new = tree(self.home), tree(self.new)

    def tearDown(self):
        self._tmp.cleanup()

    def test_swaps_and_keeps_backups(self):
        moved = server.swap_items(self.home, self.new, ITEMS, "T1")
        got = tree(self.home)
        self.assertEqual(got["memory/data"], "NEW")
        self.assertEqual(got["state.json"], "new-state")
        self.assertEqual(got["memory.before-prune-T1/data"], "OLD")
        self.assertEqual(got["state.json.before-prune-T1"], "old-state")
        self.assertEqual(got["agents/keep"], "x", "files outside the swapped items are untouched")
        self.assertEqual(len(moved), 3)

    def test_first_swap_into_an_empty_home_makes_no_backup(self):
        empty = self.home.parent / "empty"
        empty.mkdir()
        self.assertEqual(server.swap_items(empty, self.new, ITEMS, "T1"), [])
        self.assertEqual(tree(empty)["memory/data"], "NEW")

    def _fail_when(self, predicate):
        def fake(path, target):
            if predicate(path, Path(target)):
                raise PermissionError("injected failure")
            return _real_rename(path, target)

        return mock.patch.object(Path, "rename", autospec=True, side_effect=fake)

    def test_failure_while_installing_rolls_everything_back(self):
        # All three live items are already backed up and the first built item is in when this one fails.
        with self._fail_when(lambda p, t: p.name == "state.json" and p.parent == self.new):
            with self.assertRaises(RuntimeError) as ctx:
                server.swap_items(self.home, self.new, ITEMS, "T1")
        self.assertIn("rolled back", str(ctx.exception))
        self.assertEqual(tree(self.home), self.before_home, "the live memory is exactly as it was")
        self.assertEqual(tree(self.new), self.before_new, "the build is back where it was")

    def test_failure_while_backing_up_rolls_back(self):
        with self._fail_when(lambda p, t: p.name == "state.json" and p.parent == self.home):
            with self.assertRaises(RuntimeError):
                server.swap_items(self.home, self.new, ITEMS, "T1")
        self.assertEqual(tree(self.home), self.before_home)
        self.assertEqual(tree(self.new), self.before_new)

    def test_failed_rollback_is_reported_not_hidden(self):
        # The install fails, and so does putting the first built item back.
        def broken(p, t):
            return (p.name == "state.json" and p.parent == self.new) or (p.name == "memory" and t.parent == self.new)

        with self._fail_when(broken):
            with self.assertRaises(RuntimeError) as ctx:
                server.swap_items(self.home, self.new, ITEMS, "T1")
        self.assertIn("rollback was incomplete", str(ctx.exception))

    def test_existing_backup_name_is_refused_before_anything_moves(self):
        (self.home / "state.json.before-prune-T1").write_text("in the way")
        with self.assertRaises(RuntimeError):
            server.swap_items(self.home, self.new, ITEMS, "T1")
        self.assertEqual(tree(self.new), self.before_new)
        self.assertEqual(self.home.joinpath("memory/data").read_text(), "OLD")
        self.assertEqual(self.home.joinpath("state.json").read_text(), "old-state")

    def test_build_without_a_memory_is_refused_untouched(self):
        (self.new / "memory" / "data").unlink()
        (self.new / "memory").rmdir()
        with self.assertRaises(RuntimeError):
            server.swap_items(self.home, self.new, ITEMS, "T1")
        self.assertEqual(tree(self.home), self.before_home)


class Parsers(unittest.TestCase):
    def test_session_rows(self):
        rows = [
            "[2026-11-22T16:35:12.556Z \u2192 2026-11-22T16:49:18.826Z] claude BaaRai/ARTEX 151 turns agent-a8536c36af19c1097",
            "[2026-08-16] claude_code rahulsaipandit/AgentX 37 turns 1a75f479-faca-4141-a467-fc3ebcef6836",
        ]
        m = server.ROW_RE.match(rows[0])
        self.assertEqual((m["harness"], m["repo"], m["turns"], m["id"]), ("claude", "BaaRai/ARTEX", "151", "agent-a8536c36af19c1097"))
        m = server.ROW_RE.match(rows[1])
        self.assertEqual((m["repo"], m["turns"]), ("rahulsaipandit/AgentX", "37"))
        self.assertIsNone(server.ROW_RE.match("  <fork-boilerplate> You are a worker fork."))

    def test_offset_footer(self):
        self.assertEqual(server.OFFSET_RE.search("3 of 357 sessions \u2014 354 older: continue with --offset 3, or narrow").group(1), "3")

    def test_status_numbers(self):
        text = "memory: D:\\funes\\memory\nchunks: 87164\nsessions: 270\n62666 chunks awaiting embedding\n"
        got = {k: int(rx.search(text)[1]) for k, rx in server.STATUS_NUM.items() if rx.search(text)}
        self.assertEqual(got, {"chunks": 87164, "sessions": 270, "awaiting": 62666})

    def test_progress_lines(self):
        def after(line):
            server.progress_from_line(line)
            return server.JOB["stage"], server.JOB["done"], server.JOB["total"], server.JOB["rate"]

        self.assertEqual(after("[2/9] s2 (repo) \u2014 1 new of 1 chunks"), ("reading", 2, 9, None))
        self.assertEqual(after("text: embedded 14417/15765  (18/s)"), ("embedding", 14417, 15765, 18))
        self.assertEqual(after("compacting the memory\u2026")[0], "compacting")
        self.assertEqual(after("building text search index\u2026")[0], "indexing")


class LiveHome(unittest.TestCase):
    def setUp(self):
        server._LIVE_HOME.clear()
        self.addCleanup(server._LIVE_HOME.clear)

    def test_funes_status_is_asked_once(self):
        out = mock.Mock(stdout="memory: D:\\funes\\memory\nchunks: 1\n")
        with mock.patch.object(server, "run_funes", return_value=out) as run:
            self.assertEqual(server.live_home(), Path("D:\\funes"))
            server.live_home()
            server.live_home()
            self.assertEqual(run.call_count, 1)
            server.live_home(refresh=True)
            self.assertEqual(run.call_count, 2)

    def test_unreadable_status_is_an_error_not_a_cached_guess(self):
        with mock.patch.object(server, "run_funes", return_value=mock.Mock(stdout="no index yet")):
            with self.assertRaises(RuntimeError):
                server.live_home()
        self.assertNotIn("home", server._LIVE_HOME)


class Converter(unittest.TestCase):
    def test_found_wherever_the_integration_keeps_it(self):
        with tempfile.TemporaryDirectory() as home:
            script = Path(home) / ".funes" / "agents" / "claude" / "any-plugin-layout" / "funes" / "convert"
            script.parent.mkdir(parents=True)
            script.write_text("#!/bin/sh\n")
            with mock.patch.object(Path, "home", return_value=Path(home)):
                self.assertEqual(server.converter(), script)

    def test_none_when_the_integration_ships_no_converter(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".funes" / "agents" / "claude").mkdir(parents=True)
            with mock.patch.object(Path, "home", return_value=Path(home)):
                self.assertIsNone(server.converter())
            with mock.patch.object(Path, "home", return_value=Path(home) / "nowhere"):
                self.assertIsNone(server.converter())

    def test_every_supported_harness_has_a_transcript_root(self):
        for harness, root in server.TRANSCRIPT_ROOTS.items():
            self.assertIsInstance(root(), Path, harness)


class LiveFormat(unittest.TestCase):
    """Runs the real funes, so a change to the text the page parses fails here, not silently in the
    page. Skipped when funes or a memory is not available."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.status = server.run_funes(["status"], timeout=60)
            cls.sessions = server.run_funes(["sessions", "--limit", "5"], timeout=60)
        except (OSError, Exception) as e:  # noqa: BLE001 - no funes to run
            raise unittest.SkipTest(f"funes not runnable: {e}")
        if "memory:" not in cls.status.stdout or cls.sessions.returncode != 0:
            raise unittest.SkipTest("no funes memory to read")

    def test_status_reports_the_memory_path_and_counts(self):
        self.assertIsNotNone(server.re.search(r"^memory: .+$", self.status.stdout, server.re.M))
        self.assertIsNotNone(server.STATUS_NUM["chunks"].search(self.status.stdout))
        self.assertIsNotNone(server.STATUS_NUM["sessions"].search(self.status.stdout))

    def test_every_session_row_parses(self):
        rows = [l for l in self.sessions.stdout.splitlines() if l.startswith("[")]
        if not rows:
            self.skipTest("the memory has no sessions")
        for line in rows:
            self.assertIsNotNone(server.ROW_RE.match(line), f"the sessions row format changed: {line!r}")


if __name__ == "__main__":
    unittest.main()
