"""Conversation memory: what an agent remembers between turns.

Mechanics, in order:
  1. Each conversation belongs to one agent configuration and has an id.
  2. A turn is everything one question produced: the user message, the model's
     tool calls, the tool results and the final answer. Turns are stored whole,
     so a remembered tool call always has its result.
  3. On the next question the remembered turns are replayed to the model ahead
     of the new question, so it can use earlier answers and tool results.
  4. Memory is a sliding window: only the most recent `max_turns` turns are
     replayed. Older turns are dropped whole and counted, never cut in half.
  5. A turn that failed (model error) is not remembered.
  6. Conversations expire after `ttl_s` seconds without use. Nothing is written
     to disk: memory lives in the backend process and is gone on restart.

Benchmark runs never use memory: every benchmark question starts fresh.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Conversation:
    conversation_id: str
    config: str
    turns: list[list[dict]] = field(default_factory=list)  # each turn is a list of messages
    questions: list[str] = field(default_factory=list)
    total_turns: int = 0
    last_used: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)


class ConversationMemory:
    def __init__(self, max_turns: int = 6, ttl_s: float = 3600.0, max_conversations: int = 500):
        self.max_turns = max_turns
        self.ttl_s = ttl_s
        self.max_conversations = max_conversations
        self._items: dict[str, Conversation] = {}
        self._lock = threading.Lock()

    def start(self, config: str) -> Conversation:
        with self._lock:
            now = time.monotonic()
            self._items = {k: c for k, c in self._items.items() if now - c.last_used < self.ttl_s}
            while len(self._items) >= self.max_conversations:  # drop the least recently used
                del self._items[min(self._items, key=lambda k: self._items[k].last_used)]
            conversation = Conversation(uuid.uuid4().hex[:16], config)
            self._items[conversation.conversation_id] = conversation
            return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        with self._lock:
            conversation = self._items.get(conversation_id)
            if conversation and time.monotonic() - conversation.last_used >= self.ttl_s:
                del self._items[conversation_id]
                return None
            return conversation

    def history(self, conversation: Conversation) -> list[dict]:
        """The remembered messages, oldest first, ready to replay to the model."""
        return [message for turn in conversation.turns for message in turn]

    def remember(self, conversation: Conversation, question: str, turn_messages: list[dict]) -> None:
        conversation.turns.append(turn_messages)
        conversation.questions.append(question)
        conversation.total_turns += 1
        if self.max_turns >= 0:
            keep = max(self.max_turns, 0)
            conversation.turns = conversation.turns[len(conversation.turns) - keep:] if keep else []
            conversation.questions = conversation.questions[len(conversation.questions) - keep:] if keep else []
        conversation.last_used = time.monotonic()

    def describe(self, conversation: Conversation) -> dict:
        return {
            "conversation_id": conversation.conversation_id, "config": conversation.config,
            "turn": conversation.total_turns, "remembered_turns": len(conversation.turns),
            "dropped_turns": conversation.total_turns - len(conversation.turns),
            "window": self.max_turns, "remembered_questions": list(conversation.questions),
            "remembered_messages": sum(len(t) for t in conversation.turns),
        }

    def forget(self, conversation_id: str) -> bool:
        with self._lock:
            return self._items.pop(conversation_id, None) is not None
