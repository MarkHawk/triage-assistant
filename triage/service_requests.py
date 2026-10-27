"""Account requests are not faults: an unlock, a new user, a password reset, a change of access.

When a ticket asks for one of these and no runbook matches strongly, code answers it: a short reply that
points to the account administrator, no diagnostic questions, and no model. A ticket that also describes a
real fault matches a runbook strongly and goes the normal way instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .answer import Answer

_I = re.IGNORECASE
# (kind, what to ask the administrator to do, pattern). The first that matches wins, so a lockout is
# an unlock even when the ticket also mentions a password.
_PATTERNS = [
    ("unlock", "unlock the account",
     re.compile(r"\bunlock\b|\blocked\s+out\b|\baccount\s+(?:is\s+|has\s+been\s+|got\s+)?lock(?:ed|out)\b"
                r"|\b(?:account|user(?:name)?|login)\b[^.?!\n]{0,30}\blocked\b", _I)),
    ("reset-password", "reset the password",
     re.compile(r"\bpassword\s+reset\b|\breset\b[^.?!\n]{0,25}\bpassword\b|\bforgot(?:ten)?\b[^.?!\n]{0,20}\bpassword\b"
                r"|\bchange\s+my\s+password\b", _I)),
    ("new-account", "set up the new account",
     re.compile(r"\b(?:add|create|set\s*up|onboard|register)\b[^.?!\n]{0,40}\b(?:new\s+)?(?:user|account|starter|joiner|member|colleague|employee)\b"
                r"|\bnew\s+(?:starter|joiner|employee)\b", _I)),
    ("access-change", "change the access",
     re.compile(r"\b(?:add|put|move)\b[^.?!\n]{0,50}\b(?:to|into|in)\b[^.?!\n]{0,25}\b(?:group|team|role)\b"
                r"|\b(?:grant|give)\b[^.?!\n]{0,30}\baccess\b|\b(?:remove|revoke|disable)\b[^.?!\n]{0,25}\b(?:access|account|user)\b"
                r"|\b(?:access|account)\b[^.?!\n]{0,25}\b(?:removed|revoked|disabled|deleted)\b|\bleaver\b|\bneeds?\s+access\s+to\b", _I)),
]


@dataclass(frozen=True)
class ServiceRequest:
    kind: str
    action: str  # what the account administrator is asked to do, as a verb phrase


def detect(ticket: str) -> ServiceRequest | None:
    for kind, action, pattern in _PATTERNS:
        if pattern.search(ticket):
            return ServiceRequest(kind, action)
    return None


def build_answer(request: ServiceRequest, persona: str = "vendor") -> Answer:
    if persona == "internal":
        reply = (f"Thank you for getting in touch. This is an account request rather than a fault. The account administrators "
                 f"handle these, so please raise it with them and ask them to {request.action}. If that does not sort it out, "
                 "or if other people are affected too, let us know and we will look into it.")
    else:
        reply = (f"Thank you for getting in touch. This looks like an account request rather than a fault with the sign-in "
                 f"service. Please ask your account administrator to {request.action}. If that does not sort it out, or if "
                 "other people are affected too, let us know and we will look into it.")
    return Answer(
        mode="request", outcome="not_a_lab_fault", confidence="medium", likely_cause="", evidence=[], runbook=None,
        fix_steps=[f"Pass the request to the account administrator: {request.action}."], customer_reply=reply,
        questions_for_customer=[], possibilities=[],
        warnings=["Recognised as an account request from the wording of the ticket; no model was used."],
        source="offline", kind="service_request",
    )
