import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nuri.config import PaseoConfig
from nuri.tasks.gemini import run_gemini


class GeminiTest(unittest.TestCase):
    @patch("nuri.tasks.gemini.executable", return_value="agy.exe")
    @patch("nuri.tasks.gemini.subprocess.run")
    def test_google_login_preserves_conversation(self, launch, _executable):
        launch.return_value = SimpleNamespace(returncode=0, stdout=(
            '{"status":"SUCCESS","response":"요약: 확인했습니다.","conversation_id":"running-session"}'
        ))
        result = run_gemini(PaseoConfig(provider="gemini", agent_id="running-session"), "확인")
        self.assertIn("running-session", launch.call_args.args[0])
        self.assertNotIn("GEMINI_API_KEY", launch.call_args.kwargs["env"])
        self.assertEqual(result.agent_id, "running-session")

    @patch("nuri.tasks.gemini.subprocess.run")
    def test_missing_second_account_never_falls_back_to_current_google_login(self, launch):
        result = run_gemini(PaseoConfig(provider="gemini-2"), "확인")
        launch.assert_not_called()
        self.assertIn("연결되지 않았습니다", result.text)
