"""WorkPilot package initialization, before any future model SDK import."""

import os

# Fail closed even when the inherited environment tries to enable tracing.
os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
