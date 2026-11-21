import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nuri.core.dreams import AWAKE_INTERVAL, DreamBook, DreamRunner


class DreamBookTest(unittest.TestCase):
    def test_awake_roles_claim_independently_and_are_due_within_a_minute(self):
        with tempfile.TemporaryDirectory() as root:
            now = 1000
            book = DreamBook(Path(root) / "dreams.sqlite3", lambda: now)
            try:
                for role in ("assistant", "nuri"):
                    book.set_autonomy(role, True)
                now += 15
                first = book.claim("assistant")
                second = book.claim("nuri")
                self.assertEqual((first[0], second[0]), ("assistant", "nuri"))
                book.finish(*first[:2], "도우미 기록", "project")
                book.finish(*second[:2], "누리 기록", "project")
                now += AWAKE_INTERVAL - 1
                self.assertIsNone(book.claim("assistant"))
                now += 1
                self.assertLess(AWAKE_INTERVAL, 60)
                self.assertEqual(book.claim("assistant")[2], "도우미 기록")
                self.assertEqual(book.claim("nuri")[2], "누리 기록")
            finally:
                book.db.close()

    def test_user_work_preempts_a_running_project(self):
        with tempfile.TemporaryDirectory() as root:
            now = 1000
            book = DreamBook(Path(root) / "dreams.sqlite3", lambda: now)
            book.set_autonomy("assistant", True)
            now += 15
            started = threading.Event()
            preempted = threading.Event()
            work = threading.Event()
            completed = threading.Event()
            plans = []

            def fake_project(exe, role, previous, memory, plan, active):
                plans.append(plan)
                if len(plans) == 2:
                    completed.set()
                    return "완성한 자료"
                started.set()
                limit = time.monotonic() + 3
                while active() and time.monotonic() < limit:
                    time.sleep(0.01)
                if not active():
                    preempted.set()
                return ""

            with (
                patch("nuri.core.dreams.project_plan_text", return_value="저장할 작업") as choose,
                patch("nuri.core.dreams.project_text", fake_project),
            ):
                runner = DreamRunner(book, work.is_set)
                try:
                    self.assertTrue(started.wait(5))
                    work.set()
                    self.assertTrue(preempted.wait(2))
                    self.assertFalse(completed.wait(2.2))
                    self.assertEqual(book.status("assistant")["projects"], [])
                    self.assertEqual(book.project_plan("assistant"), "저장할 작업")
                    work.clear()
                    self.assertTrue(completed.wait(3))
                    deadline = time.monotonic() + 2
                    while not book.status("assistant")["projects"] and time.monotonic() < deadline:
                        time.sleep(0.01)
                    choose.assert_called_once()
                finally:
                    runner.close()
            self.assertEqual(plans, ["저장할 작업", "저장할 작업"])
            self.assertEqual(book.status("assistant")["projects"][0]["text"], "완성한 자료")
            self.assertEqual(book.project_plan("assistant"), "")
            self.assertFalse(book.status("assistant")["error"])
            book.db.close()

    def test_two_sleeping_roles_have_separate_memory_and_ten_minute_budget(self):
        with tempfile.TemporaryDirectory() as root:
            now = 1000
            path = Path(root) / "dreams.sqlite3"
            book = DreamBook(path, lambda: now)
            try:
                book.set_memory("assistant", "밤의 정원")
                book.set_memory("nuri", "톱니바퀴")
                book.set_sleeping("assistant", True)
                book.set_sleeping("nuri", True)
                now = 1599
                self.assertIsNone(book.claim())
                now = 1600
                assistant = book.claim()
                nuri = book.claim()
                self.assertEqual((assistant[0], assistant[3]), ("assistant", "밤의 정원"))
                self.assertEqual((nuri[0], nuri[3]), ("nuri", "톱니바퀴"))
                book.finish(*assistant[:2], "꽃을 그린 꿈")
                book.set_sleeping("nuri", False)
                book.finish(*nuri[:2], "깨운 뒤 완료한 꿈")
                self.assertEqual(book.status("nuri")["dreams"], [])
                self.assertIsNone(book.claim())
            finally:
                book.db.close()
            book = DreamBook(path, lambda: now)
            try:
                now = 2200
                self.assertEqual(book.claim()[2:], ("꽃을 그린 꿈", "밤의 정원", "dream"))
            finally:
                book.db.close()

    def test_awake_projects_keep_separate_history_across_sleep_and_restart(self):
        with tempfile.TemporaryDirectory() as root:
            now = 1000
            path = Path(root) / "dreams.sqlite3"
            book = DreamBook(path, lambda: now)
            book.set_memory("nuri", "작은 교량")
            book.set_autonomy("nuri", True)
            now += 15
            role, revision, previous, memory, kind = book.claim()
            self.assertEqual((role, previous, memory, kind), ("nuri", "", "작은 교량", "project"))
            book.finish(role, revision, "교량 설계 초안", kind)
            book.set_sleeping("nuri", True)
            now += 600
            role, revision, _, _, kind = book.claim()
            self.assertEqual(kind, "dream")
            book.set_sleeping("nuri", False)
            book.finish(role, revision, "깨운 뒤 도착한 꿈", kind)
            book.db.close()
            book = DreamBook(path, lambda: now)
            try:
                self.assertEqual(book.status("nuri")["dreams"], [])
                self.assertEqual(book.status("assistant")["projects"], [])
                now += 15
                self.assertEqual(book.claim()[2:], ("교량 설계 초안", "작은 교량", "project"))
            finally:
                book.db.close()

    def test_restart_retries_the_saved_plan_without_waiting_for_the_next_interval(self):
        with tempfile.TemporaryDirectory() as root:
            now = 1000
            path = Path(root) / "dreams.sqlite3"
            book = DreamBook(path, lambda: now)
            book.set_autonomy("assistant", True)
            now += 15
            role, revision, _, _, _ = book.claim()
            self.assertTrue(book.save_project_plan(role, revision, "설계를 이어가기"))
            book.db.close()
            book = DreamBook(path, lambda: now)
            try:
                self.assertEqual(book.claim()[0], "assistant")
                self.assertEqual(book.project_plan("assistant"), "설계를 이어가기")
                self.assertEqual(book.project_plan("nuri"), "")
            finally:
                book.db.close()
