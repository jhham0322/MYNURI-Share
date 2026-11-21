import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from nuri.core.schedule import ScheduleBook


class ScheduleBookTest(unittest.TestCase):
    def test_persisted_offsets_do_not_interrupt_local_schedules(self):
        now = datetime(2026, 11, 20, 9, 0, 10)
        rows = [
            {"id": "offset-once", "kind": "once", "at": "2026-11-20T09:00:00+09:00"},
            {"id": "offset-daily", "kind": "daily", "time": "09:00:00+09:00"},
            {"id": "local-once", "kind": "once", "at": "2026-11-20T09:00:00"},
            {"id": "local-daily", "kind": "daily", "time": "09:00"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedules.json"
            path.write_text(json.dumps({"schedules": rows}), encoding="utf-8")
            book = ScheduleBook(path)

            self.assertEqual(
                [item.id for item in book.ordered(now)], ["local-daily", "local-once"]
            )
            self.assertEqual(
                [item.id for item in book.take_due(now, 60)], ["local-once", "local-daily"]
            )
            self.assertEqual(ScheduleBook(path).take_due(now, 60), [])
