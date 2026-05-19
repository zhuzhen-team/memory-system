"""Profile self-learning module.

Public surface (imported eagerly so call sites can do
``from memoryd.profile import rewrite_identity_weekly`` etc.):

- :class:`ProfileStore` / :class:`ProfileVersion` — DAO + dataclass
- :func:`ensure_profile_schema` — idempotent table creation
- :func:`rewrite_identity_weekly` — weekly LLM rewrite
- :func:`read_current_identity` — reader used by SessionStart hook
- :func:`generate_monthly_change_report` — monthly evolution report
- :func:`increment_trigger` / :func:`top_triggers` / :func:`render_trends_section` — trigger frequency
- Path helpers: :func:`identity_path`, :func:`identity_history_dir`,
  :func:`change_reports_dir`
"""
from .evolution import generate_monthly_change_report
from .identity import (
    change_reports_dir,
    identity_history_dir,
    identity_path,
    read_current_identity,
    rewrite_identity_weekly,
)
from .migrations import ensure_profile_schema, profile_schema_sql
from .store import ProfileStore, ProfileVersion
from .trends import (
    increment_trigger,
    increment_triggers,
    rising_triggers,
    recall_hot,
    render_trends_section,
    top_triggers,
)

__all__ = [
    "ProfileStore",
    "ProfileVersion",
    "change_reports_dir",
    "ensure_profile_schema",
    "generate_monthly_change_report",
    "identity_history_dir",
    "identity_path",
    "increment_trigger",
    "increment_triggers",
    "profile_schema_sql",
    "read_current_identity",
    "recall_hot",
    "render_trends_section",
    "rewrite_identity_weekly",
    "rising_triggers",
    "top_triggers",
]
