"""Short conversation memory, so follow-ups like «а до якого числа?» work.

Kept in process memory, keyed by chat id (never by username). It's lost on
restart, which is fine: it only helps follow-up questions within a conversation.
"""

from collections import deque
from dataclasses import dataclass


@dataclass
class Turn:
    question: str
    answer: str


class ChatMemory:
    def __init__(self, max_turns: int) -> None:
        self._max_turns = max_turns
        self._chats: dict[int, deque[Turn]] = {}

    def turns(self, chat_id: int) -> list[Turn]:
        return list(self._chats.get(chat_id, ()))

    def last_question(self, chat_id: int) -> str | None:
        turns = self._chats.get(chat_id)
        return turns[-1].question if turns else None

    def add(self, chat_id: int, question: str, answer: str) -> None:
        turns = self._chats.setdefault(chat_id, deque(maxlen=self._max_turns))
        turns.append(Turn(question, answer))
