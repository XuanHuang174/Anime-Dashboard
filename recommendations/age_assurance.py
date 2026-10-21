"""Closed local authority port. No production verifier selected or contacted.

The future adapter must read authoritative local results, not client assertions
or remote identity material while holding transaction locks. No env setting can
register an arbitrary verifier in N2-1. Tests replace this port in isolation.
"""
from dataclasses import dataclass
from datetime import datetime


class AgeAssuranceUnavailable(RuntimeError):
    """A future local adapter may explicitly signal unavailability; never allow."""


@dataclass(frozen=True)
class AgeAssurance:
    subject_id: int
    status: str = 'unavailable'
    provider: str = ''
    reference: str = ''
    version: int = 0
    valid_until: datetime | None = None


def current_age_assurance(user_id):
    return AgeAssurance(subject_id=user_id)
