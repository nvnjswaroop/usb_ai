"""Dependency injection container.

Factories for every tool used by the FastAPI app. Routers request dependencies
via FastAPI's Depends() mechanism. Tests can override via dependency_overrides.

All tool instantiation goes through the Container. Module-level globals in
main.py are replaced by the DI container pattern.

Why a single Container: most tools share paths and the LLM engine. Passing the
container once is simpler than threading five deps through each handler.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Paths:
    """Resolved project directory paths."""
    root:    Path
    models:  Path
    history: Path
    output:  Path
    whisper: Path
    ui:      Path


@dataclass
class Container:
    """All tool instances + the LLM engine + paths. Single source of truth
    for FastAPI dependency injection."""
    paths:   Paths
    llm:     object   # LLMEngine typed as object to avoid circular import
    file:    object
    ppt:     object
    pdf:     object
    code:    object
    voice:   object
    vscode:  object
    image:   object
    diff:    object
    export:  object
    agent:   object


def build_paths(root: Optional[Path] = None) -> Paths:
    """Resolve the standard project paths. `root` overrides for tests."""
    r = Path(root) if root else Path(__file__).resolve().parent.parent
    paths = Paths(
        root=r,
        models=r / "models",
        history=r / "history",
        output=r / "output",
        whisper=r / "whisper_models",
        ui=Path(__file__).resolve().parent / "ui",
    )
    for d in (paths.models, paths.history, paths.output, paths.whisper):
        d.mkdir(parents=True, exist_ok=True)
    return paths


def build_default(paths: Optional[Paths] = None) -> Container:
    """Construct a fully-wired container with production defaults.

    Imports are deferred inside the function so importing `container` doesn't
    drag in heavy deps (llama_cpp, whisper, etc.).
    """
    # All tool imports are local to keep import cost low.
    from llm               import LLMEngine
    from tools.file_tool   import FileTool
    from tools.ppt_tool    import PPTTool
    from tools.pdf_tool    import PDFTool
    from tools.code_tool   import CodeTool
    from tools.voice_tool  import VoiceTool
    from tools.vscode_tool import VSCodeTool
    from tools.image_tool  import ImageTool
    from tools.diff_tool   import DiffTool
    from tools.export_tool import ExportTool
    from tools.agent_tool  import AgentTool

    paths = paths or build_paths()

    return Container(
        paths=paths,
        llm   =LLMEngine(paths.models, paths.root / "prefeeds"),
        file  =FileTool(),
        ppt   =PPTTool(paths.output),
        pdf   =PDFTool(),
        code  =CodeTool(python_path=sys.executable),
        voice =VoiceTool(),
        vscode=VSCodeTool(paths.output),
        image =ImageTool(paths.output),
        diff  =DiffTool(),
        export=ExportTool(paths.output),
        agent =AgentTool(paths.output),
    )
