"""The backend interface. Every request passes the secrets guard before it leaves."""

from __future__ import annotations

from ..guard import Guard
from ..prompt import Budget, Prompt


class BackendError(Exception):
    """A backend could not give an answer. The message says what to do about it."""


class Refused(BackendError):
    """The model declined to answer."""


class PromptTruncated(BackendError):
    """The model server cut the prompt short, so the answer would be based on part of it."""


class NoRecordedAnswer(BackendError):
    """Demo mode has nothing saved for this input."""


class Backend:
    name = "base"
    model = ""
    offline = False  # True: clarify-mode answers are built by code and no model is called

    def __init__(self, guard: Guard, budget: Budget):
        self.guard = guard
        self.budget = budget
        self.last_stats: dict = {}  # what the server reported about the last request, for the run footer

    def complete(self, prompt: Prompt, schema: dict) -> dict:
        """Check the whole prompt for secrets, then ask the model. Raises SecretFound before sending."""
        self.guard.check_outbound(untrusted=prompt.untrusted, trusted=[*prompt.trusted, prompt.system, prompt.user_trusted_check()])
        return self._complete(prompt, schema)

    def _complete(self, prompt: Prompt, schema: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError
