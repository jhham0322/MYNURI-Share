import json
import unittest
from unittest.mock import patch

from nuri.__main__ import _build_paseo_task, _run_paseo_with_fallback
from nuri.config import PaseoConfig
from nuri.tasks.base import TaskResult
from nuri.tasks.paseo import MSG_FAILED, PaseoTask


class PaseoFallbackTest(unittest.TestCase):
    def test_normal_requests_reuse_an_agent_and_rotate_after_twelve_turns(self):
        calls = []

        class Settings:
            def task_config(self):
                return PaseoConfig(provider="codex")

        def task(config):
            def run(command):
                calls.append((config.agent_id, config.keep_agents))
                agent = config.agent_id or f"agent-{len(calls)}"
                return TaskResult("done", provider=config.provider, agent_id=agent, agent_reply_chars=4)
            return run

        with patch("nuri.tasks.paseo.PaseoTask", task):
            run = _build_paseo_task(Settings())
            for _ in range(13):
                run("test")

        self.assertEqual(calls, [(None, 0)] + [("agent-1", 0)] * 11 + [(None, 0)])

    def test_phone_conversations_do_not_share_agents(self):
        class Settings:
            def task_config(self):
                return PaseoConfig(provider="codex", agent_id="desktop-agent")

        calls = []

        def task(cfg):
            def run(command):
                calls.append(cfg.agent_id)
                return TaskResult("done", provider=cfg.provider, agent_id=cfg.agent_id or str(len(calls)))
            return run

        with patch("nuri.tasks.paseo.PaseoTask", task):
            run = _build_paseo_task(Settings())
            for role in ("assistant", "nuri", "assistant", "nuri", None):
                run("test", conversation=role)
        self.assertEqual(calls, [None, None, "1", "2", "desktop-agent"])

    def test_start_error_with_agent_id_does_not_retry_on_alternate_provider(self):
        for output_stream in ("stdout", "stderr"):
            with self.subTest(output_stream=output_stream):
                calls = []
                failure = json.dumps(
                    {"agentId": "already-created", "message": "provider unavailable"}
                )

                def runner(args, timeout):
                    calls.append(args[1])
                    return (
                        1,
                        failure if output_stream == "stdout" else "",
                        failure if output_stream == "stderr" else "",
                    )

                cfg = PaseoConfig(provider="codebuddy-code", keep_agents=0)
                with self.assertLogs("nuri.tasks.paseo", level="ERROR"):
                    result = _run_paseo_with_fallback(
                        cfg, "test command", lambda current: PaseoTask(current, runner)
                    )
                self.assertEqual(calls, ["run"])
                self.assertEqual(result.text, MSG_FAILED)
                self.assertFalse(result.retry_safe)

    def test_unavailable_provider_without_agent_id_can_fall_back(self):
        calls = []

        def runner(args, timeout):
            calls.append(args[1])
            if args[1] == "run":
                provider = args[args.index("--provider") + 1]
                if provider == "codebuddy-code":
                    return 1, "", '{"message":"provider unavailable"}'
                self.assertEqual(provider, "codex")
                return 0, '{"agentId":"alternate"}', ""
            if args[1] == "wait":
                return 0, '{"status":"idle"}', ""
            self.assertEqual(args[1], "logs")
            return 0, "Completed.", ""

        cfg = PaseoConfig(provider="codebuddy-code", keep_agents=0)
        with self.assertLogs("nuri.tasks.paseo", level="WARNING"):
            result = _run_paseo_with_fallback(
                cfg, "test command", lambda current: PaseoTask(current, runner)
            )
        self.assertEqual(calls, ["run", "run", "wait", "logs"])
        self.assertEqual(result.text, "Completed.")
        self.assertEqual(result.provider, "codex")
        self.assertEqual(result.fallback_from, "codebuddy-code")
