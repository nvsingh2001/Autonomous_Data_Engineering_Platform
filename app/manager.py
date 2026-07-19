"""Web-process conversation state for the intent intake.

Run state, logs, and the approval gate live in Redis (app/run_store) so the
Celery workers can share them; what remains here is the only state that never
leaves the web tier — the intake chat history and its LangSmith thread id.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.core import new_thread_id


class IntentSession:
    def __init__(self):
        self.intent_history: list[dict] = []
        self.business_intent: dict = {}
        self.intent_thread_id = ""

    def begin_intent_thread(self) -> str:
        """A fresh intake conversation starts a fresh LangSmith thread."""
        self.intent_thread_id = new_thread_id("intent")
        return self.intent_thread_id

    def ensure_intent_thread(self) -> str:
        """The current intent conversation's thread, created if the conversation
        was started without /api/intent/start."""
        if not self.intent_thread_id:
            self.intent_thread_id = new_thread_id("intent")
        return self.intent_thread_id


mgr = IntentSession()
