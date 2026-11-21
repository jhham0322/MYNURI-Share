"""paseo CLI 로 명령을 실제 에이전트에게 실행시키는 태스크.

흐름(새 에이전트):
    paseo run --json --no-wait  → 에이전트 생성 후 agentId 확보
    paseo wait <id> --timeout N → idle 이 될 때까지 대기(무한 대기 방지)
    paseo logs <id> --filter text --tail 1 → 마지막 응답 텍스트 회수

흐름(기존 에이전트 재사용, config.agent_id 지정 시):
    paseo send <id> --prompt <명령> → 응답이 끝날 때까지 대기
    paseo logs <id> --filter text --tail 1 → 마지막 응답 텍스트 회수

회수한 텍스트는 TTS 로 읽기 좋게 정리한다(마크다운 제거). 결과가 길면 10초 분량으로
함축한다: 요약이 없거나 길면 같은 에이전트에게 요약만 다시 요청한다. 다만 뉴스·브리핑·
전문 낭독 요청(`is_full_read_request`)은 항목이 여러 개라 요약만 읽으면 앞부분만 듣고
끝나므로, 모든 항목을 담은 본문을 전문 낭독 한도(`full_read_seconds`)까지 읽는다.

응답에 `기억:` 줄이 있으면 그 사실들을 결과와 함께 돌려준다(`TaskResult.notes`).
앱은 그것을 기억 저장소에 따로 남겨 다음 대화에서 참고한다.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess

import numpy as np

from nuri.config import PaseoConfig
from nuri.nlu.intent import is_full_read_request
from nuri.tasks.base import CommandRunner, TaskResult, resolve_executable, subprocess_runner

logger = logging.getLogger("nuri.tasks.paseo")

_CODE_FENCE = re.compile(r"```.*?(?:```|$)", re.DOTALL)
# 표 행은 음성으로 읽으면 의미가 사라지므로 발췌 대상에서 뺀다
_TABLE_ROW = re.compile(r"^\s*\|.*$", re.MULTILINE)
_MARKDOWN = re.compile(r"[`*#>|]+")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_LIST_MARK = re.compile(r"^\s*[-•]\s*", re.MULTILINE)
_SPACES = re.compile(r"\s+")
# 에이전트가 남기는 음성용 요약 줄(`요약: ...`, `**요약:** ...`, `Summary: ...`)
_SUMMARY_LABEL = re.compile(r"^\s*(?:음성\s*)?(?:요약|summary)\s*[:：]\s*(.+)$", re.IGNORECASE)
# 다시 쓸 만한 사실을 표시하는 줄(`기억: ...`). 음성으로 읽지 않고 기억 저장소에만 남긴다.
_MEMORY_LABEL = re.compile(r"^\s*(?:기억|기억할\s*것|memory)\s*[:：]\s*(.+)$", re.IGNORECASE)
# "기억: 없음" 처럼 남길 사실이 없다는 표시
_NO_NOTE = re.compile(r"(?:없음|없습니다|해당\s*없음|none|-+)", re.IGNORECASE)
# CLI의 text 필터는 사용자 입력과 내부 사고도 포함할 수 있다.
_NON_ANSWER = re.compile(r"^\s*\[(?:user|thought|message received)\](?:\s|$)", re.IGNORECASE)

# 사용자에게 음성으로 안내하는 고정 문구
MSG_STT_MISSING = "음성을 글로 바꾸지 못했어요. STT 설정을 확인해 주세요."
MSG_EMPTY_COMMAND = "무슨 일을 할지 말씀해 주세요."
MSG_NO_REPLY = "결과를 확인하지 못했어요. 작업 수행 여부를 확인해 주세요."
MSG_TIMEOUT = "작업이 시간 안에 끝나지 않았어요. 조금 뒤에 결과를 확인해 주세요."
MSG_PERMISSION = "권한 승인이 필요해서 멈췄어요. paseo 에서 승인해 주세요."
MSG_FAILED = "지시를 처리하지 못했어요. paseo 상태를 확인해 주세요."
MSG_SUMMARY_LEAD = "요약해서 말씀드릴게요."
MSG_SUMMARY_UNAVAILABLE = "결과를 짧게 요약하지 못했어요. paseo에서 확인해 주세요."

# wait/send 가 돌려주는 상태 값. `send` 는 턴이 끝나면 idle 대신 completed 를 준다.
_DONE_STATUSES = {"", "idle", "completed", "done"}
_FAILED_STATUSES = {"error", "failed", "cancelled", "canceled", "aborted"}


class PermissionPending(Exception):
    """에이전트가 권한 승인 대기(permission) 상태로 멈춘 경우."""


class ProviderUnavailable(Exception):
    """실행 전에 제공자 사용 불가가 확인된 경우."""


_UNAVAILABLE = re.compile(
    r"(?:quota|rate[ _-]?limit|usage[ _-]?limit|insufficient[_ -]?(?:quota|credits)|"
    r"resource.exhausted|too many requests|\b429\b|provider.{0,30}(?:unavailable|not available|not installed)|"
    r"(?:usage|token|daily).{0,30}(?:exceeded|exhausted)|사용량.{0,20}(?:초과|제한)|할당량.{0,20}(?:초과|소진))",
    re.IGNORECASE,
)


def _json_field(raw: str, key: str) -> str | None:
    """CLI 의 JSON 출력에서 문자열 필드를 꺼낸다. 형식이 다르면 None."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    return None if value is None else str(value)


def _strip_markup(text: str) -> str:
    """마크다운/코드블록/링크 문법을 걷어내고 한 줄로 정리한다."""
    body = _CODE_FENCE.sub(" ", text)
    body = _TABLE_ROW.sub(" ", body)
    body = _LINK.sub(r"\1", body)
    body = _MARKDOWN.sub(" ", body)
    body = _LIST_MARK.sub("", body)
    return _SPACES.sub(" ", body).strip()


def _fit_sentences(body: str, max_chars: int) -> str:
    """길이를 넘으면 문장 경계에서 자른다(음성이 문장 중간에서 끊기지 않도록)."""
    if len(body) <= max_chars:
        return body

    cut = body[:max_chars]
    boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if boundary > max_chars // 3:
        return cut[: boundary + 1].strip()
    # 문장 경계가 없으면 단어 경계에서 자른다(단어 중간에서 끊기지 않도록)
    space = cut.rfind(" ")
    if space > max_chars // 2:
        cut = cut[:space]
    return cut.strip()


def clean_for_speech(text: str, max_chars: int) -> str:
    """에이전트 응답을 음성 안내용 문장으로 정리한다."""
    return _fit_sentences(_strip_markup(text), max_chars)


def _parse_reply(text: str) -> tuple[str, str, tuple[str, ...]]:
    """응답을 (본문, 요약, 기억할 사실들) 로 나눈다.

    `요약:` 줄은 음성으로 읽을 문장, `기억:` 줄은 기억 저장소에 남길 사실이다.
    두 줄 모두 본문에서 빼야 음성으로 읽을 때 라벨이 섞이지 않는다.
    """
    body_lines: list[str] = []
    summary = ""
    notes: list[str] = []
    for line in text.splitlines():
        # 굵게/백틱 표기를 걷어낸 뒤 라벨인지 본다
        plain = line.replace("*", "").replace("`", "").strip()
        match = _SUMMARY_LABEL.match(plain)
        if match:
            summary = match.group(1).strip()  # 여러 줄이면 마지막 것을 쓴다
            continue
        match = _MEMORY_LABEL.match(plain)
        if match:
            value = match.group(1).strip()
            if value and value not in notes:
                notes.append(value)
            continue
        body_lines.append(line)
    return "\n".join(body_lines), summary, tuple(notes)


def _split_summary(text: str) -> tuple[str, str]:
    """응답을 (본문, 요약) 으로 나눈다. `요약:` 줄이 없으면 요약은 빈 문자열."""
    body, summary, _notes = _parse_reply(text)
    return body, summary


def _summary_for_speech(summary: str, max_chars: int) -> str | None:
    """요약을 음성 한도에 맞춘다. 문장이 한도 안에서 끝나야 읽는다.

    에이전트는 요청한 글자 수를 조금 넘겨 쓰는 일이 흔하다(예: 70자 요청에 80자).
    그때마다 재요약을 요청하면 수십 초를 한 번 더 기다려야 하므로, 첫 문장이
    한도 안에서 끝나면 그 문장만 읽는다. 문장 중간에서 끊기는 경우에만 None.
    """
    body = _strip_markup(summary)
    if len(body) <= max_chars:
        return body
    cut = body[:max_chars]
    boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if boundary <= max_chars // 3:
        return None
    return cut[: boundary + 1].strip()


def condense_for_speech(text: str, max_chars: int, lead: str = "", full: bool = False) -> str:
    """응답을 음성 안내 분량으로 함축한다.

    기본은 10초 분량: 짧은 본문이나 요약 줄을 우선 쓰고, 요약이 없거나 문장
    중간에서 끊겨야 하면 요약 실패로 알린다(호출부가 재요약을 요청한다).

    `full=True`(뉴스·브리핑·전문 낭독)이면 요약 줄 대신 **본문 전체**를 읽는다.
    요약만 읽으면 첫 항목만 들려주고 끝나기 때문이다.
    """
    body_text, summary, _notes = _parse_reply(text)
    body = _strip_markup(body_text)
    if full:
        source = body or _strip_markup(summary)
        return _fit_sentences(source, max_chars) if source else ""
    if body and len(body) <= max_chars:
        return body
    if not summary and not body:
        return ""

    if summary:
        fitted = _summary_for_speech(summary, max_chars)
        if fitted:
            return fitted
    return MSG_SUMMARY_UNAVAILABLE


class PaseoTask:
    """`Task` 인터페이스(명령 → 안내 문구)를 paseo 로 구현한 실행기."""

    def __init__(self, cfg: PaseoConfig, runner: CommandRunner | None = None) -> None:
        self.cfg = cfg
        self._run = runner or subprocess_runner
        self._exe = resolve_executable(cfg.executable)

    def __call__(self, command: str | np.ndarray) -> TaskResult:
        if isinstance(command, np.ndarray):
            return TaskResult(MSG_STT_MISSING)
        text = command.strip()
        if not text:
            return TaskResult(MSG_EMPTY_COMMAND)

        try:
            reply, agent_id = self._execute(text)
            if reply.strip().casefold() == "no activity to display.":
                logger.warning("paseo가 결과 대신 빈 활동 표시를 반환했습니다")
                return TaskResult(MSG_NO_REPLY, provider=self.cfg.provider)
            notes = self._notes(reply)
            # 뉴스·브리핑은 항목이 여러 개라 10초 요약으로 줄이면 앞부분만 듣고 끝난다.
            full = is_full_read_request(text)
            limit = self.cfg.max_full_chars if full else self.cfg.max_reply_chars
            spoken = condense_for_speech(reply, limit, full=full)
            if full and reply and len(_strip_markup(reply)) > limit:
                logger.warning("전문 낭독 한도 초과(%d자) → 문장 경계에서 끊어 읽음", limit)
            if reply and not full and spoken == MSG_SUMMARY_UNAVAILABLE:
                try:
                    spoken = self._retry_summary(agent_id)
                except Exception as exc:  # noqa: BLE001 - 원래 작업 결과는 유지한다
                    logger.warning("음성 요약 재요청 실패: %s", exc)
                    spoken = MSG_SUMMARY_UNAVAILABLE
        except PermissionPending:
            logger.warning("paseo 에이전트가 권한 승인 대기 상태")
            return TaskResult(MSG_PERMISSION, provider=self.cfg.provider)
        except ProviderUnavailable:
            logger.warning("제공자를 사용할 수 없어 실행 전에 중단: %s", self.cfg.provider)
            return TaskResult(MSG_FAILED, provider=self.cfg.provider, retry_safe=True)
        except (subprocess.TimeoutExpired, TimeoutError):
            logger.warning("paseo 응답 대기 시간 초과")
            return TaskResult(MSG_TIMEOUT, provider=self.cfg.provider)
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 음성으로 안내한다
            logger.exception("paseo 작업 실패: %s", exc)
            return TaskResult(MSG_FAILED, provider=self.cfg.provider)
        finally:
            # 명령마다 에이전트가 쌓이므로, 끝나면 오래된 것부터 정리한다
            self._prune_agents()

        if not reply:
            return TaskResult(MSG_NO_REPLY, provider=self.cfg.provider)
        return TaskResult(
            spoken or MSG_NO_REPLY, notes, provider=self.cfg.provider,
            agent_id=agent_id, agent_reply_chars=len(reply),
        )

    # --- 내부 단계 ---
    def _notes(self, text: str) -> tuple[str, ...]:
        """응답의 `기억:` 줄에서 다시 쓸 만한 사실만 뽑는다(최대 `max_notes`개)."""
        notes: list[str] = []
        for raw in _parse_reply(text)[2]:
            value = _strip_markup(raw)[: self.cfg.note_max_chars].strip()
            if not value or _NO_NOTE.fullmatch(value):
                continue
            if value not in notes:
                notes.append(value)
            if len(notes) >= self.cfg.max_notes:
                break
        return tuple(notes)

    def _prompt(self, text: str) -> str:
        """에이전트에게 보낼 지시문.

        기본은 10초 분량 요약: 음성으로는 긴 결과를 다 읽을 수 없으므로, 답변 맨
        마지막 줄에 짧은 요약을 덧붙이도록 함께 요청한다. 뉴스·브리핑처럼 전문을
        들려줘야 하는 요청(`is_full_read_request`)은 10초로 줄이면 첫 항목만 듣고
        끝나므로, 모든 항목을 담은 말하듯 이어 쓴 문장을 요청한다.

        나중에 다시 쓸 사실이 있으면 `기억:` 줄로 표시하게 해, 요약에서 중요한
        사항을 기억 저장소에 따로 남긴다.

        주의: `paseo run` 은 프롬프트의 **첫 줄만** 전달한다(둘째 줄 이하는 무시됨).
        따라서 지시문은 줄바꿈 없이 한 줄로 이어 붙인다.
        """
        if not self.cfg.request_summary:
            return text
        full = is_full_read_request(text)
        chars = self.cfg.max_full_chars if full else self.cfg.max_reply_chars
        hint = self.cfg.full_read_instruction if full else self.cfg.summary_instruction
        hint = hint.replace("{chars}", str(chars))
        hint = hint.replace("{notes}", str(self.cfg.max_notes))
        return f"{text} {hint}"

    def _execute(self, text: str) -> tuple[str, str]:
        prompt = self._prompt(text)
        if self.cfg.agent_id:
            # send 는 완료까지 블로킹이므로 별도 wait 가 필요 없다
            agent_id = self._send(prompt)
        else:
            agent_id = self._start_agent(prompt)
            self._wait_idle(agent_id)
        reply = self._last_text(agent_id)
        # 실행된 도구가 없다는 구조화 기록이 있을 때만 빈 응답을 다른 처리기로 넘긴다.
        empty_reply = not reply.strip() or reply.strip().casefold() == "no activity to display."
        if empty_reply and not self.cfg.agent_id and self._proven_no_tools(agent_id):
            raise ProviderUnavailable("응답이 없고 실행된 도구가 없습니다")
        if len(reply) <= 500 and _UNAVAILABLE.search(reply) and self._proven_no_tools(agent_id):
            raise ProviderUnavailable("제공자 사용량 제한으로 작업이 시작되지 않았습니다")
        return reply, agent_id

    def _proven_no_tools(self, agent_id: str) -> bool:
        """도구 실행이 없다는 구조화 기록이 있을 때만 재시도를 허용한다."""
        try:
            code, out, _ = self._run(
                [self._exe, "logs", agent_id, "--filter", "tools", "--json"],
                self.cfg.log_timeout_seconds,
            )
            if code != 0:
                return False
            data = json.loads(out)
            return isinstance(data, list) and not data
        except (ValueError, OSError, subprocess.TimeoutExpired, TimeoutError):
            return False

    def _retry_summary(self, agent_id: str) -> str:
        """원래 작업을 반복하지 않고 기존 결과만 다시 요약한다."""
        prompt = (
            "방금 완료한 작업의 결과를 음성으로 전달합니다. 도구 사용이나 작업 재실행 없이 "
            f"핵심 결과와 실패 또는 필요한 후속 조치를 {self.cfg.max_reply_chars}자 이내의 "
            "완결된 한국어 한두 문장으로만 요약하세요."
        )
        self._send(prompt, agent_id)
        text = self._last_text(agent_id)
        body, summary = _split_summary(text)
        if summary:
            result = _summary_for_speech(summary, self.cfg.max_reply_chars)
        else:
            # 요약 줄 없이 본문만 돌아온 경우는 요약으로 보지 않는다(본문을 잘라 읽지 않는다)
            plain = _strip_markup(body)
            result = plain if plain and len(plain) <= self.cfg.max_reply_chars else None
        if not result:
            logger.warning("음성 요약이 비어 있거나 문장 중간에서 끊김")
            return MSG_SUMMARY_UNAVAILABLE
        return result

    def _start_agent(self, text: str) -> str:
        """새 에이전트를 만들어 명령을 맡기고 agentId 를 돌려준다."""
        args = [
            self._exe,
            "run",
            "--json",
            "--no-wait",
            "--provider",
            self.cfg.provider,
            "--title",
            self.cfg.title,
        ]
        if self.cfg.mode:
            # 음성으로는 승인을 받을 수 없으므로, 묻지 않는 모드를 지정한다
            args += ["--mode", self.cfg.mode]
        if self.cfg.workspace_id:
            args += ["--workspace", self.cfg.workspace_id]
        elif self.cfg.cwd:
            args += ["--cwd", self.cfg.cwd]
        args.append(text)

        code, out, err = self._run(args, self.cfg.start_timeout_seconds)
        # 작업공간이 사라진 실행 전 오류에만 작업 디렉터리로 다시 연결한다.
        if (
            code != 0
            and self.cfg.workspace_id
            and self.cfg.cwd
            and not _json_field(out, "agentId")
            and not _json_field(err, "agentId")
            and '"WORKSPACE_NOT_FOUND"' in (out + err)
        ):
            logger.warning("지정된 작업공간이 없어 작업 디렉터리로 다시 연결합니다")
            index = args.index("--workspace")
            args[index:index + 2] = ["--cwd", self.cfg.cwd]
            code, out, err = self._run(args, self.cfg.start_timeout_seconds)
        if code != 0:
            # 실행 요청의 명확한 제공자 거절만 재시도한다. ID가 있으면 이미 시작됐을 수 있다.
            if (
                not _json_field(out, "agentId")
                and not _json_field(err, "agentId")
                and _UNAVAILABLE.search(err or out)
            ):
                raise ProviderUnavailable(err or out)
            raise RuntimeError(f"paseo run 실패({code}): {err.strip() or out.strip()}")
        agent_id = _json_field(out, "agentId")
        if not agent_id:
            if _json_field(out, "status") in _FAILED_STATUSES and _UNAVAILABLE.search(out):
                raise ProviderUnavailable(out)
            raise RuntimeError(f"paseo run 출력에 agentId 없음: {out.strip()[:200]}")
        logger.info("paseo 에이전트 생성: %s", agent_id)
        return agent_id

    def _send(self, text: str, agent_id: str | None = None) -> str:
        """이미 떠 있는 에이전트에 명령을 보내고 끝날 때까지 기다린다.

        `send` 는 기본적으로 완료까지 블로킹이므로 프로세스 타임아웃으로 상한을 건다.
        (별도 wait 를 쓰지 않아 직전 응답을 잘못 읽는 경쟁 조건도 피한다)
        """
        agent_id = agent_id or str(self.cfg.agent_id)
        code, out, err = self._run(
            [self._exe, "send", agent_id, "--json", "--prompt", text],
            self.cfg.wait_timeout_seconds,
        )
        if code != 0:
            raise RuntimeError(f"paseo send 실패({code}): {err.strip() or out.strip()}")
        self._check_status(out)
        return agent_id

    def _wait_idle(self, agent_id: str) -> None:
        """에이전트가 idle 이 될 때까지 기다린다. 상한을 넘기면 TimeoutError."""
        code, out, err = self._run(
            [
                self._exe,
                "wait",
                agent_id,
                "--json",
                "--timeout",
                str(int(self.cfg.wait_timeout_seconds)),
            ],
            # CLI 자체 타임아웃보다 살짝 여유를 둬서 CLI 가 먼저 끝나도록 한다
            self.cfg.wait_timeout_seconds + 15.0,
        )
        if code != 0:
            raise RuntimeError(f"paseo wait 실패({code}): {err.strip() or out.strip()}")
        if (_json_field(out, "status") or "").lower() in _FAILED_STATUSES and _UNAVAILABLE.search(out):
            if self._proven_no_tools(agent_id):
                raise ProviderUnavailable("제공자가 요청을 거절했습니다")
        self._check_status(out)

    @staticmethod
    def _check_status(raw: str) -> None:
        """wait/send 응답의 status 를 해석한다.

        `paseo send` 는 그 턴이 끝나면 status 를 `completed` 로 돌려준다. 이것을
        "idle 이 아니다" 로 뭉뚱그리면 작업이 끝났는데도 시간 초과로 처리해
        결과를 버리게 되므로, 끝난 상태(idle/completed)는 성공으로 본다.
        permission 은 "시간 초과"가 아니라 승인 대기이므로 구분해 알린다.
        """
        status = (_json_field(raw, "status") or "").lower()
        if status == "permission":
            raise PermissionPending(status)
        if status in _DONE_STATUSES:
            return
        if status in _FAILED_STATUSES:
            raise RuntimeError(f"에이전트가 실패 상태로 끝남: {status}")
        if status:
            raise TimeoutError(f"에이전트가 idle 이 아님: {status}")

    def _last_text(self, agent_id: str) -> str:
        """에이전트의 마지막 응답 텍스트를 가져온다."""
        code, out, err = self._run(
            [
                self._exe,
                "logs",
                agent_id,
                "--filter",
                "text",
                "--tail",
                str(self.cfg.log_tail),
            ],
            self.cfg.log_timeout_seconds,
        )
        if code != 0:
            raise RuntimeError(f"paseo logs 실패({code}): {err.strip() or out.strip()}")
        text = out.strip()
        if _NON_ANSWER.match(text):
            logger.warning("paseo 마지막 텍스트가 응답이 아닌 입력 또는 내부 기록입니다")
            return ""
        return text

    # --- 에이전트 정리 ---
    def _prune_agents(self) -> None:
        """누리가 만든 에이전트를 최신 N개만 남기고 삭제한다.

        `paseo ls -a --json` 은 최신순이므로 앞에서 N개를 남긴다(보관된 것도 포함해
        목록에 남지 않게 한다). 정리 실패는 음성 안내에 영향을 주지 않도록 넘어간다.
        """
        keep = self.cfg.keep_agents
        if keep <= 0:
            return
        try:
            code, out, err = self._run(
                [self._exe, "ls", "-a", "--json"], self.cfg.prune_timeout_seconds
            )
            if code != 0:
                logger.warning("paseo 목록 조회 실패(%s): %s", code, err.strip() or out.strip())
                return
            agents = json.loads(out)
            if not isinstance(agents, list):
                return
            mine = [agent for agent in agents if self._is_own_agent(agent)]
            for agent in mine[keep:]:
                self._delete_agent(str(agent.get("id") or ""))
        except Exception as exc:  # noqa: BLE001 - 정리 실패는 치명적이지 않다
            logger.debug("에이전트 정리 중 오류 무시: %s", exc)

    def _is_own_agent(self, agent: object) -> bool:
        """누리가 만든 에이전트인지(제목 접두사 기준) 판별한다."""
        if not isinstance(agent, dict):
            return False
        if self.cfg.agent_id and agent.get("id") == self.cfg.agent_id:
            return False  # 재사용 중인 에이전트는 지우지 않는다
        return str(agent.get("name") or "").startswith(self.cfg.title)

    def _delete_agent(self, agent_id: str) -> None:
        if not agent_id:
            return
        code, _, err = self._run(
            [self._exe, "delete", agent_id], self.cfg.prune_timeout_seconds
        )
        if code == 0:
            logger.info("오래된 누리 에이전트 정리: %s", agent_id)
        else:
            logger.warning("에이전트 삭제 실패(%s): %s", code, err.strip())
