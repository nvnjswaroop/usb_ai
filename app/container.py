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


def _backend_label(backend: str) -> str:
    return ("USB_AI_BACKEND=server (default)" if backend == "server"
            else "USB_AI_BACKEND=inline — legacy in-process engine")


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

    ponytail: LLM backend selectable via USB_AI_BACKEND=server|inline.
    Phase C: 'server' (llama-server sidecar) is the DEFAULT — it killed the
    cp311 wheel lockstep and enables concurrent inference. 'inline' remains
    as a legacy escape hatch requiring llama-cpp-python==0.3.19 on py3.11.
    Both engines expose the same public surface — see llm_server.ServerEngine
    docstring for the contract and tests/test_sidecar.py::TestInterfaceParity
    for the guard.
    """
    # All tool imports are local to keep import cost low.
    import os as _os
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

    backend = (_os.environ.get("USB_AI_BACKEND", "server").strip().lower()
               or "server")
    if backend == "server":
        from llm_server import ServerEngine
        llm = ServerEngine(paths.models, paths.root / "prefeeds")
        _note = f"llama-server sidecar (binary: {llm.manager.binary})"
    else:
        llm = LLMEngine(paths.models, paths.root / "prefeeds")
        _note = "in-process llama-cpp-python"
    print(f"[BACKEND] {_backend_label(backend)} — {_note}", flush=True)

    # Build tools once, share them: the agent gets the SAME instances the
    # routers use, so config (e.g. CodeTool.timeout) applies everywhere.
    file   = FileTool()
    ppt    = PPTTool(paths.output)
    pdf    = PDFTool()
    code   = CodeTool(python_path=sys.executable)
    voice  = VoiceTool()
    vscode = VSCodeTool(paths.output)
    image  = ImageTool(paths.output)
    diff   = DiffTool()
    export = ExportTool(paths.output)

    return Container(
        paths=paths,
        llm   =llm,
        file  =file,
        ppt   =ppt,
        pdf   =pdf,
        code  =code,
        voice =voice,
        vscode=vscode,
        image =image,
        diff  =diff,
        export=export,
        agent =AgentTool(paths.output, file_tool=file, code_tool=code,
                         pdf_tool=pdf, diff_tool=diff, vscode_tool=vscode,
                         export_tool=export, ppt_tool=ppt, voice_tool=voice),
    )
