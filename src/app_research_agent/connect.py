"""One-time setup: connect the search toolkit (Exa) to the agent's Composio user.

Uses the project's existing API_KEY auth config for the toolkit (or creates a
Composio-managed one) and registers the key from the environment as a
connected account. The key is read from EXA_API_KEY and never printed.
Interactive alternative: the Connect Link the agent prints when not connected.
"""

from __future__ import annotations

import os

from .config import settings


def connect_search_toolkit() -> str:
    from composio import Composio
    from composio.types import auth_scheme

    tk = settings.search_toolkit
    key = os.getenv(f"{tk.upper()}_API_KEY")
    if not key:
        raise SystemExit(f"{tk.upper()}_API_KEY is not set in .env")
    composio = Composio()
    session = composio.create(user_id=settings.composio_user_id, toolkits=[tk])
    state = session.toolkits(toolkits=[tk]).items
    if state and state[0].connection and state[0].connection.is_active:
        return f"{tk} already connected for user '{settings.composio_user_id}'"

    configs = [
        a for a in composio.auth_configs.list(toolkit_slug=tk).items if a.auth_scheme == "API_KEY"
    ]
    if configs:
        auth_config_id = configs[0].id
    else:
        auth_config_id = composio.auth_configs.create(
            tk, {"type": "use_custom_auth", "auth_scheme": "API_KEY", "credentials": {}}
        ).id
    req = composio.connected_accounts.initiate(
        user_id=settings.composio_user_id,
        auth_config_id=auth_config_id,
        config=auth_scheme.api_key({"generic_api_key": key}),
    )
    return f"{tk} connected for user '{settings.composio_user_id}' (connected account {req.id}, status {req.status})"
