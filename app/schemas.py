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