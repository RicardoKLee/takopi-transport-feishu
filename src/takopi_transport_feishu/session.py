from __future__ import annotations

from dataclasses import dataclass, field

from takopi.model import EngineId, ResumeToken

__all__ = ["SessionStore"]


@dataclass(slots=True)
class SessionStore:
    _sessions: dict[str, dict[EngineId, ResumeToken]] = field(default_factory=dict)

    def session_key(self, *, chat_id: str, thread_id: str | None) -> str:
        if thread_id:
            return f"{chat_id}:{thread_id}"
        return chat_id

    def get(
        self,
        *,
        chat_id: str,
        thread_id: str | None,
        engine: EngineId,
    ) -> ResumeToken | None:
        bucket = self._sessions.get(
            self.session_key(chat_id=chat_id, thread_id=thread_id)
        )
        if bucket is None:
            return None
        token = bucket.get(engine)
        if token is None or token.engine != engine:
            return None
        return token

    def set(
        self,
        *,
        chat_id: str,
        thread_id: str | None,
        token: ResumeToken,
    ) -> None:
        key = self.session_key(chat_id=chat_id, thread_id=thread_id)
        bucket = self._sessions.setdefault(key, {})
        bucket[token.engine] = token

    def clear(
        self,
        *,
        chat_id: str,
        thread_id: str | None,
        engine: EngineId | None = None,
    ) -> None:
        key = self.session_key(chat_id=chat_id, thread_id=thread_id)
        if engine is None:
            self._sessions.pop(key, None)
            return
        bucket = self._sessions.get(key)
        if bucket is not None:
            bucket.pop(engine, None)
            if not bucket:
                self._sessions.pop(key, None)
