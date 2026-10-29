"""The talker runs on your Claude subscription, never on API billing: checked before it starts."""

import json
import os
import subprocess
from pathlib import Path

import claude_agent_sdk
from loguru import logger

API_BILLING_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)


def ensure_subscription_auth():
    """Refuse to run on API billing unless VOICE_ALLOW_API=1 is set explicitly."""
    if os.environ.get("VOICE_ALLOW_API") == "1":
        logger.warning("VOICE_ALLOW_API=1: API billing allowed")
        return
    if os.environ.get("ANTHROPIC_BASE_URL") == "https://api.anthropic.com":
        del os.environ["ANTHROPIC_BASE_URL"]  # just the default endpoint, set in your shell profile
    for key in API_BILLING_ENV:
        if os.environ.pop(key, None) is not None:
            logger.warning(f"Ignoring {key} (set VOICE_ALLOW_API=1 to use API billing)")
    # Ask the same CLI binary the SDK will run how it's authenticated.
    cli = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    status = json.loads(subprocess.run([str(cli), "auth", "status"], capture_output=True, text=True).stdout)
    if status.get("authMethod") != "claude.ai":
        raise SystemExit(f"Not on the claude.ai subscription (authMethod={status.get('authMethod')!r}). Refusing to start.")
