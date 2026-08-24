from pydantic import BaseModel, Field
from typing import Optional, List, Dict
class Artifact(BaseModel):
    type: str  # "code" | "ppt" | "image" | "html" | "markdown" | "file"
    title: str
    description: str = ""
    content: Optional[str] = None
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    mime_type: str = "text/plain"
    preview: Optional[str] = None
    metadata: Dict = Field(default_factory=dict)
    trace: List[Dict] = Field(default_factory=list)


class ToolCall(BaseModel):
    tool: str
    params: Dict
    thought: str = ""


class AgentStep(BaseModel):
    step: int
    tool: str
    params: Dict
    thought: str = ""
    observation: str = ""
    success: bool = False
    artifacts: List[Artifact] = Field(default_factory=list)


class AgentResult(BaseModel):
    status: str
    result: str
    artifacts: List[Artifact] = Field(default_factory=list)
    steps: List[Dict] = Field(default_factory=list)
    total_steps: int = 0
    # ponytail: estimated total tokens spent (prompt + completion) across all
    # agent iterations including revise/self-correction rounds. Heuristic is
    # chars//4 — llama-cpp's token-count API isn't always exposed. Pure estimate.
    total_tokens_used: int = 0
    # ponytail: cap on tokens spent before the agent gives up. Default 8k —
    # roughly 32 LLM calls at 256 tokens each. Prevents runaway costs when the
    # LLM gets stuck in a revise spiral.
    token_cap_reached: bool = False


# ── Tool-call param validation ────────────────────────────────────────────────
# ponytail: lives in schemas (stdlib+pydantic only) so tests can import the
# REAL validator without dragging agent_tool's transitive deps (python-pptx via
# tools.ppt_tool) into a barebones venv. Previously test_security.py kept a
# hand-mirrored copy that could silently drift from production.

NO_SCHEMA_TOOLS = frozenset({"finish", "revise", "retry"})


def validate_params(tool_name: str, params, allowed: Dict[str, set],
                    no_schema=None) -> bool:
    """Reject agent tool calls with undeclared param keys.

    - Management tools (finish/revise/retry) always pass — no manifest body.
    - Unknown tools pass (the dispatcher reports "[ERROR] Unknown tool").
    - Known tools: params must be a dict whose keys are a subset of the
      manifest-declared keys in allowed[tool_name].
    Closes the "LLM invents a field" hole at the parse gate.
    """
    if no_schema is None:
        no_schema = NO_SCHEMA_TOOLS
    if tool_name in no_schema:
        return True
    if tool_name not in allowed:
        return True  # unknown tool -> dispatcher surfaces it
    if not isinstance(params, dict):
        return False
    return set(params.keys()).issubset(allowed[tool_name])