"""WorkPilot package initialization, before any future model SDK import."""

import os

# Fail closed even when the inherited environment tries to enable tracing.
os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
os.environ["OPENAI_AGENTS_DONT_LOG_MODEL_DATA"] = "1"
os.environ["OPENAI_AGENTS_DONT_LOG_TOOL_DATA"] = "1"
