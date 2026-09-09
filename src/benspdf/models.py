"""
Ollama model selection.

Kept separate from the MCP client because picking a model has nothing to do
with the protocol, and a pure function is easy to test on its own.
"""

import os
from typing import List, Optional

MODEL_ENV_VAR = "BENSPDF_MODEL"


def resolve_model(requested: Optional[str], available: List[str]) -> str:
    """
    Pick which Ollama model to use.

    Precedence: the requested model, then $BENSPDF_MODEL, then the first
    model installed locally.

    Args:
        requested: Explicitly requested model, or None
        available: Model names installed locally, as reported by Ollama

    Returns:
        The name of the model to use, including its tag

    Raises:
        ValueError: If nothing is installed, or the requested model isn't
            among the installed ones.
    """
    if not available:
        raise ValueError(
            "No Ollama models installed. Pull one first, e.g.:\n"
            "   ollama pull llama3.1"
        )

    choice = requested or os.environ.get(MODEL_ENV_VAR)
    if choice is None:
        return available[0]

    # Accept both "llama3.1" and the fully tagged "llama3.1:latest".
    for name in available:
        if name == choice or name.split(":")[0] == choice:
            return name

    raise ValueError(
        f"Model {choice!r} is not installed.\n"
        f"   Available: {', '.join(available)}\n"
        f"   Pull it with: ollama pull {choice}"
    )
