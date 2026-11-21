import tempfile
import unittest
import uuid
from pathlib import Path

from nuri.mobile import MobileCommands


class MobileCheckpointTest(unittest.TestCase):
    def test_restart_preserves_queued_work_and_completed_phase(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "commands.sqlite3"
            work = MobileCommands(path)
            request_id = str(uuid.uuid4())
            text = "계속 작업: 테스트 목표"
            self.assertEqual(work.submit({"request_id": request_id, "text": text, "input_type": "text"})[0], 202)
            work.db.close()
            work = MobileCommands(path)
            self.assertEqual(work.take(), (request_id, text))
            work.checkpoint(request_id, 0, "codex", "저장한 계획")
            work.db.close()
            work = MobileCommands(path)
            self.assertEqual(work.take(), (request_id, text))
            self.assertEqual(work.checkpoints(request_id), [("codex", "저장한 계획")])
            work.db.close()
            work = MobileCommands(path)
            self.assertIsNone(work.take())
            self.assertEqual(work.get(request_id)[1]["status"], "unknown")
            work.db.close()
