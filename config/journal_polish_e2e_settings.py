"""Local synthetic polish QA only; same isolated database as the A/B browser tests."""
from .watch_history_e2e_settings import *  # noqa: F403

RECOMMENDATION_SERIES_ENABLED = True
RECOMMENDATION_SERIES_CANARY_USERS = []
RECOMMENDATION_CREDITS_VOICE_ACTOR_ENABLED = True
RECOMMENDATION_CREDITS_STUDIO_ENABLED = True
RECOMMENDATION_CREDITS_CANARY_USERS = []
