"""누리 전용 기억 저장소(JSONL)와 경량 검색.

외부 의존성 없이 동작해야 하므로 임베딩 대신 두 단계로 찾는다.
1) **슬롯 조회**: "내 이름 뭐야" 처럼 주제가 분명한 질문은 주제로 바로 찾는다.
   (조사가 바뀌면 "이름은/이름이" 의 문자 bigram 이 끊겨 유사도가 떨어지기 때문)
2) **bigram TF-IDF 코사인**: 주제를 특정할 수 없는 질문의 폴백. 동점에 가까운
   결과는 신뢰하지 않는다(1.3배 우위 규칙).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nuri.rag.extract import slot_of

logger = logging.getLogger("nuri.rag")

_STRIP = re.compile(r"[\s,.!?~·\"'…]+")
_DOMINANCE = 1.3  # 1위가 2위보다 이만큼 높아야 채택


def normalize(text: str) -> str:
    """자모 분리(NFD) 등으로 들어온 한글을 비교 가능한 형태로 맞춘다."""
    return unicodedata.normalize("NFC", text).strip()


def _has_jong(word: str) -> bool:
    """마지막 글자에 받침이 있는지(조사 선택용)."""
    if not word:
        return False
    last = word[-1]
    if "가" <= last <= "힣":
        return (ord(last) - 0xAC00) % 28 != 0
    return False


def josa(word: str, with_jong: str, without_jong: str) -> str:
    """받침 유무에 맞는 조사를 고른다(은/는, 이/가, 이라고/라고)."""
    return with_jong if _has_jong(word) else without_jong


@dataclass(frozen=True)
class Memory:
    id: str
    created: str
    kind: str  # profile | preference | rule | note
    subject: str
    value: str
    text: str
    source: str = "voice"

    def to_json(self) -> dict[str, str]:
        return {
            "id": self.id,
            "created": self.created,
            "kind": self.kind,
            "subject": self.subject,
            "value": self.value,
            "text": self.text,
            "source": self.source,
        }

    @classmethod
    def from_json(cls, data: object) -> "Memory | None":
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                id=str(data["id"]),
                created=str(data["created"]),
                kind=str(data["kind"]),
                subject=str(data.get("subject", "")),
                value=str(data.get("value", "")),
                text=str(data["text"]),
                source=str(data.get("source", "voice")),
            )
        except KeyError:
            return None


def _bigrams(text: str) -> list[str]:
    body = _STRIP.sub("", normalize(text)).lower()
    if len(body) < 2:
        return [body] if body else []
    return [body[i : i + 2] for i in range(len(body) - 1)]


def _tfidf(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    counts = Counter(tokens)
    vec = {t: (1.0 + math.log(c)) * idf.get(t, 0.0) for t, c in counts.items()}
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {t: v / norm for t, v in vec.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())


def answer_text(memory: Memory) -> str:
    """저장된 기억을 음성으로 읽을 문장으로 만든다."""
    if not memory.subject:
        return f"네, 이렇게 기억하고 있어요. {memory.text}"
    link = josa(memory.value, "이라고", "라고")
    return f"네, {memory.subject}{josa(memory.subject, '은', '는')} {memory.value}{link} 기억하고 있어요."


class MemoryStore:
    """`memories.jsonl` 한 파일에 기억을 쌓고 검색한다."""

    def __init__(self, path: Path, max_items: int = 2000, max_text_chars: int = 200) -> None:
        self.path = Path(path)
        self.max_items = max_items
        self.max_text_chars = max_text_chars
        self._items: list[Memory] = []
        self._load()

    # --- 조회 ---
    def all(self) -> list[Memory]:
        return list(self._items)

    def recent(self, limit: int = 3) -> list[Memory]:
        return list(reversed(self._items))[:limit]

    def find_subject(self, subject: str) -> Memory | None:
        for memory in reversed(self._items):
            if memory.subject == subject:
                return memory
        return None

    def search(
        self, query: str, limit: int = 3, min_score: float = 0.30
    ) -> list[tuple[Memory, float]]:
        """bigram TF-IDF 코사인으로 관련 기억을 찾는다(점수 높은 순)."""
        if not self._items:
            return []
        query_tokens = _bigrams(query)
        if not query_tokens:
            return []

        docs = [_bigrams(f"{m.subject} {m.value} {m.text}") for m in self._items]
        df: Counter[str] = Counter()
        for tokens in docs:
            df.update(set(tokens))
        total = len(docs)
        idf = {t: math.log((total + 1) / (count + 1)) + 1.0 for t, count in df.items()}

        query_vec = _tfidf(query_tokens, idf)
        scored: list[tuple[Memory, float]] = []
        for memory, tokens in zip(self._items, docs):
            score = _cosine(query_vec, _tfidf(tokens, idf))
            if score >= min_score:
                scored.append((memory, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def answer(self, query: str, min_score: float = 0.30) -> str | None:
        """질문에 답할 기억을 찾아 문장으로 돌려준다. 확신이 없으면 None."""
        subject = slot_of(query)
        if subject:
            memory = self.find_subject(subject)
            return answer_text(memory) if memory else None

        hits = self.search(query, limit=2, min_score=min_score)
        if not hits:
            return None
        if len(hits) > 1 and hits[0][1] < hits[1][1] * _DOMINANCE:
            logger.info("기억 검색 결과가 근소해 채택하지 않음(%.2f vs %.2f)", hits[0][1], hits[1][1])
            return None
        return answer_text(hits[0][0])

    # --- 변경 ---
    def add(
        self,
        kind: str,
        value: str,
        text: str,
        subject: str = "",
        source: str = "voice",
    ) -> bool:
        """기억을 저장한다. 같은 주제(또는 같은 문장)가 있으면 최신 값으로 바꾼다."""
        value = normalize(value)[: self.max_text_chars]
        text = normalize(text)[: self.max_text_chars]
        subject = normalize(subject)
        if not text:
            return False

        previous = self._items
        self._items = [
            memory
            for memory in self._items
            if not self._is_same_slot(memory, kind, subject, text)
        ]
        self._items.append(
            Memory(
                id=uuid.uuid4().hex[:12],
                created=datetime.now().isoformat(timespec="seconds"),
                kind=kind,
                subject=subject,
                value=value,
                text=text,
                source=source,
            )
        )
        if len(self._items) > self.max_items:
            self._items = self._items[-self.max_items :]
        if self._save():
            return True
        self._items = previous
        return False

    @staticmethod
    def _is_same_slot(memory: Memory, kind: str, subject: str, text: str) -> bool:
        if subject:
            return memory.subject == subject and memory.kind == kind
        return memory.text == text

    def forget(self, subject: str = "") -> int:
        """주제(없으면 전체)에 해당하는 기억을 지운다. 지운 개수를 돌려준다."""
        if subject:
            keep = [m for m in self._items if m.subject != subject]
        else:
            keep = []
        removed = len(self._items) - len(keep)
        if removed:
            self._items = keep
            self._save()
        return removed

    # --- 저장소 입출력 ---
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("기억 저장소를 읽지 못했습니다(%s): %s", self.path, exc)
            return
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                memory = Memory.from_json(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("손상된 기억 줄을 건너뜁니다: %s", line[:60])
                continue
            if memory is not None:
                self._items.append(memory)

    def _save(self) -> bool:
        """임시 파일에 쓴 뒤 교체한다(중간에 죽어도 파일이 깨지지 않게)."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                for memory in self._items:
                    handle.write(json.dumps(memory.to_json(), ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)
            return True
        except OSError as exc:
            logger.warning("기억 저장에 실패했습니다(%s): %s", self.path, exc)
            return False
