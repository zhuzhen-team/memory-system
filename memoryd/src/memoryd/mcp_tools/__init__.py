"""memoryd MCP tool handlers.

`mcp_server.py` defines thin fastmcp decorator stubs that delegate to the
async handlers in this subpackage. Splitting them out keeps the entry-point
file small enough to read and makes each tool independently testable
without touching the FastMCP machinery.

Submodules:
- ``memory``   — save / update / delete / get / search / context / timeline
- ``session``  — session_start / session_end / session_summary / capture_passive
- ``judge``    — judge / compare (entity-supersede + diff)
- ``admin``    — stats / merge_projects / current_project / doctor /
                save_prompt / suggest_topic_key
- ``util``     — shared helpers: data_root, scope resolution, error mapping
"""
from . import admin, judge, memory, session, util

__all__ = ["admin", "judge", "memory", "session", "util"]
