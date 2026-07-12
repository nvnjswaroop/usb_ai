"""
Agent Tool - autonomous goal-directed tool orchestration
LLM acts as agent: given a task + list of available tools, outputs structured JSON actions.
Implements an act → observe → act loop with self-correction (max 8 steps).
"""
import json
import re
import os
import sys
# Ensure app/ is in path for schemas import
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_TOOLS_DIR)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from schemas import Artifact, AgentResult, ToolCall
from typing import List, Dict, Optional, Any

# Reuse existing tools
from tools.file_tool    import FileTool
from tools.code_tool   import CodeTool
from tools.pdf_tool    import PDFTool
from tools.diff_tool   import DiffTool
from tools.export_tool import ExportTool
from tools.image_tool  import ImageTool
from tools.vscode_tool import VSCodeTool
from tools.ppt_tool    import PPTTool
from tools.voice_tool  import VoiceTool

import sys
if sys.platform == "win32":
    from pathlib import Path as _P
    _OUT = _P(__file__).parent.parent / "output"
else:
    from pathlib import Path
    _OUT = Path(__file__).parent.parent / "output"


# ── Tool Manifest ──────────────────────────────────────────────────────────────
TOOL_MANIFEST = [
    {
        "name": "read_file",
        "description": "Read the contents of a text file. Returns the full file content.",
        "params": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path to the file to read"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file if it doesn't exist.",
        "params": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Full path for the output file"},
                "content": {"type": "string", "description": "Content to write to the file"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "list_directory",
        "description": "List files and folders in a directory, or list all drives on the system.",
        "params": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path, or __drives__ to list drives"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "search_files",
        "description": "Search for a query string inside all text files in a directory.",
        "params": {
            "type": "object",
            "properties": {
                "path":  {"type": "string", "description": "Directory path to search in"},
                "query": {"type": "string", "description": "String to search for inside files"}
            },
            "required": ["path", "query"]
        }
    },
    {
        "name": "run_python",
        "description": "Execute Python code in a sandbox and return stdout/stderr + return code.",
        "params": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "read_pdf",
        "description": "Extract text from a PDF file at the given path.",
        "params": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path to the PDF file"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "diff_files",
        "description": "Compare two files and return a unified diff (added/removed line counts).",
        "params": {
            "type": "object",
            "properties": {
                "path_a": {"type": "string", "description": "Path to the first (original) file"},
                "path_b": {"type": "string", "description": "Path to the second (modified) file"}
            },
            "required": ["path_a", "path_b"]
        }
    },
    {
        "name": "save_code_file",
        "description": "Save a code string to a file in the USB AI output folder and optionally open in VS Code.",
        "params": {
            "type": "object",
            "properties": {
                "code":     {"type": "string", "description": "The code content to save"},
                "filename": {"type": "string", "description": "Desired filename, e.g. solution.py"},
                "language": {"type": "string", "description": "Programming language (python, javascript, etc.)"}
            },
            "required": ["code", "filename"]
        }
    },
    {
        "name": "list_outputs",
        "description": "List all files in the USB AI output directory.",
        "params": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "generate_ppt",
        "description": "Generate a PowerPoint presentation. Returns the .pptx file path.",
        "params": {
            "type": "object",
            "properties": {
                "topic":           {"type": "string", "description": "Presentation topic"},
                "num_slides":      {"type": "integer", "description": "Number of slides", "default": 6},
                "style":           {"type": "string", "description": "Style: professional, minimal, vibrant", "default": "professional"},
                "extra_instructions": {"type": "string", "description": "Additional instructions", "default": ""}
            },
            "required": ["topic"]
        }
    },
    {
        "name": "generate_image",
        "description": "Generate or process an image. For now, save uploaded image bytes to output dir.",
        "params": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Description of the image to generate"}
            },
            "required": ["description"]
        }
    },
    {
        "name": "speak_text",
        "description": "Convert text to speech and return the audio result.",
        "params": {
            "type": "object",
            "properties": {
                "text":     {"type": "string", "description": "Text to speak"},
                "rate":     {"type": "integer", "description": "Speech rate (words per minute)", "default": 175},
                "voice_id": {"type": "string", "description": "Voice ID to use", "default": ""}
            },
            "required": ["text"]
        }
    },
    {
        "name": "transcribe_audio",
        "description": "Transcribe an audio file to text using Whisper.",
        "params": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path to the audio file"}
            },
            "required": ["path"]
        }
    },
]

AGENT_PROMPT_TEMPLATE = """You are a goal-directed AI agent. You have access to these tools:

{manifest}

You MUST respond with EXACTLY one JSON object per message — no markdown fences, no explanation outside the JSON.
Your response format:

{{
  "tool": "tool_name",
  "params": {{ "param_name": "param_value", ... }},
  "thought": "Why you chose this tool and what you expect to happen."
}}

If the task is COMPLETE, respond with:
{{
  "tool": "finish",
  "result": "Final result summary",
  "thought": "What you accomplished and what artifacts were created."
}}

If a tool fails and you want to try a different approach:
{{
  "tool": "revise",
  "reason": "Why the previous approach failed.",
  "thought": "What you will do differently this time.",
  "params": {{ ... new params if applicable ... }}
}}

If a tool fails and you want to retry the same tool with different params:
{{
  "tool": "retry",
  "reason": "Why it failed.",
  "thought": "What you will do differently.",
  "params": {{ ... corrected params ... }}
}}

Start now. Task: {task}
"""


class AgentTool:
    def __init__(self, output_dir=None):
        self.output_dir = output_dir or _OUT
        self._file_tool   = FileTool()
        self._code_tool   = CodeTool(python_path=sys.executable)
        self._pdf_tool    = PDFTool()
        self._diff_tool   = DiffTool()
        self._image_tool  = ImageTool(self.output_dir)
        self._vscode_tool = VSCodeTool(self.output_dir)
        self._export_tool = ExportTool(self.output_dir)
        self._ppt_tool    = PPTTool(self.output_dir)
        self._voice_tool  = VoiceTool()

    # ── Tool Registry ─────────────────────────────────────────────────────────
    def _get_tool_fn(self, name: str):
        table = {
            "read_file":              self._file_tool.read_file,
            "write_file":            self._file_tool.write_file,
            "list_directory":        self._file_tool.list_directory,
            "search_files":          self._file_tool.search_content,
            "run_python":            self._code_tool.run_python,
            "read_pdf":              self._pdf_tool.extract_text,
            "diff_files":            self._diff_tool.diff_files,
            "save_code_file":        self._save_code_file,
            "list_outputs":          self._list_outputs,
            "generate_ppt":          self._generate_ppt,
            "generate_image":        self._generate_image,
            "speak_text":            self._speak_text,
            "transcribe_audio":      self._transcribe_audio,
        }
        return table.get(name)

    # ── Composite tool wrappers (return Artifact) ──────────────────────────────
    def _save_code_file(self, code: str, filename: str, language: str = "") -> dict:
        result = self._vscode_tool.save_and_open(code, filename, language)
        if result.get("status") == "ok":
            path = result.get("path", "")
            return {
                "status": "ok",
                "artifact": Artifact(
                    type="code",
                    title=filename,
                    content=code,
                    file_path=path,
                    file_name=filename,
                    mime_type=self._mime_for_lang(language),
                    metadata={"language": language or "text", "line_count": len(code.splitlines())},
                ).model_dump()
            }
        return result

    def _generate_ppt(self, topic: str, num_slides: int = 6,
                      style: str = "professional", extra_instructions: str = "") -> dict:
        try:
            filename, title = self._ppt_tool.create_ppt({
                "title": topic,
                "slides": [{"slide_number": i+1, "title": f"Slide {i+1}",
                           "bullet_points": [f"Point about {topic}"],
                           "speaker_notes": ""} for i in range(num_slides)]
            }, style)
            path = str(self.output_dir / filename)
            return {
                "status": "ok",
                "artifact": Artifact(
                    type="ppt",
                    title=title or topic,
                    description=f"{num_slides}-slide presentation on {topic}",
                    file_path=path,
                    file_name=filename,
                    mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    metadata={"slide_count": num_slides, "style": style},
                ).model_dump()
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _generate_image(self, description: str = "") -> dict:
        return {"status": "ok", "message": "Image generation requires an image file upload. Use file tools to work with existing images."}

    def _speak_text(self, text: str, rate: int = 175, voice_id: str = "") -> dict:
        return self._voice_tool.speak(text, rate, 1.0, voice_id)

    def _transcribe_audio(self, path: str) -> dict:
        return self._voice_tool.transcribe(path, "base", str(self.output_dir))

    def _list_outputs(self) -> dict:
        try:
            files = [{"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1)}
                     for f in sorted(self.output_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
                     if f.is_file()]
            return {"status": "ok", "files": files}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _mime_for_lang(self, lang: str) -> str:
        table = {
            "python": "text/x-python", "py": "text/x-python",
            "javascript": "text/javascript", "js": "text/javascript",
            "typescript": "text/typescript", "ts": "text/typescript",
            "html": "text/html", "css": "text/css",
            "java": "text/x-java", "cpp": "text/x-c++", "c": "text/x-c",
            "rust": "text/x-rust", "go": "text/x-go",
            "bash": "text/x-sh", "sh": "text/x-sh", "sql": "text/sql",
            "json": "application/json", "xml": "application/xml",
            "markdown": "text/markdown", "md": "text/markdown",
        }
        return table.get(lang.lower(), "text/plain")

    # ── Structured JSON Parsing ────────────────────────────────────────────────
    # ponytail: build param-key whitelist from the manifest at module load.
    # Closes the "LLM-invents-path-bypass-_resolve" hole by letting us reject
    # tool calls whose `params` have fields the manifest doesn't authorize.
    _ALLOWED_PARAMS = {
        entry["name"]: set(entry["params"]["properties"].keys())
        for entry in TOOL_MANIFEST
    }
    # Also accept any param for management tools that take no manifest body.
    _NO_SCHEMA_TOOLS = {"finish", "revise", "retry"}

    def _parse_tool_call(self, text: str) -> Optional[ToolCall]:
        """Parse a strict JSON ToolCall from LLM output. Params are validated
        against the manifest; unknown keys cause rejection (returns None)."""
        text = text.strip()

        # Try direct JSON parse (fast path)
        try:
            obj = json.loads(text)
            if "tool" in obj or "action" in obj:
                tool = obj.get("tool") or obj.get("action", "")
                params = obj.get("params") or {}
                thought = obj.get("thought", "")
                if tool and self._validate_params(tool, params):
                    return ToolCall(tool=tool, params=params, thought=thought)
        except json.JSONDecodeError:
            pass

        # Find JSON object by counting braces
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i+1])
                        tool = obj.get("tool") or obj.get("action", "")
                        params = obj.get("params") or {}
                        thought = obj.get("thought", "")
                        if tool and self._validate_params(tool, params):
                            return ToolCall(tool=tool, params=params, thought=thought)
                    except json.JSONDecodeError:
                        pass
                    break
        return None

    def _validate_params(self, tool_name: str, params: dict) -> bool:
        """Reject tool calls with undeclared param keys. Returns True to dispatch."""
        if tool_name in self._NO_SCHEMA_TOOLS:
            return True
        if tool_name not in self._ALLOWED_PARAMS:
            # Unknown tool → dispatcher will return "[ERROR] Unknown tool". Don't hide it.
            return True
        if not isinstance(params, dict):
            return False
        return set(params.keys()).issubset(self._ALLOWED_PARAMS[tool_name])

    # ── Execute a single tool ─────────────────────────────────────────────────
    def _execute_action(self, tool_name: str, params: dict) -> tuple[str, List[Artifact]]:
        """
        Execute a tool and return (observation_str, list_of_artifacts).
        """
        fn = self._get_tool_fn(tool_name)
        if fn is None:
            return f"[ERROR] Unknown tool: {tool_name}", []

        artifacts: List[Artifact] = []
        try:
            result = fn(**params)
            # Extract artifact if present
            if isinstance(result, dict):
                if "artifact" in result:
                    artifacts.append(Artifact(**result["artifact"]))
                s = json.dumps(result, ensure_ascii=False)
                if len(s) > 2000:
                    s = s[:2000] + "\n... [truncated]"
                return s, artifacts
            return str(result), artifacts
        except TypeError as e:
            return f"[ERROR] Missing parameter for {tool_name}: {e}", []
        except Exception as e:
            return f"[ERROR] {tool_name} failed: {e}", []

    # ── Main agent loop ────────────────────────────────────────────────────────
    def execute_task(self, task: str, llm_engine, max_steps: int = 8) -> AgentResult:
        """
        Run an autonomous agent loop to complete the task.
        llm_engine: an LLMEngine instance (from llm.py)
        Returns: AgentResult with artifacts[], steps[], and final result.
        """
        manifest_json = json.dumps(TOOL_MANIFEST, indent=2)
        system_prompt = (
            "You are a goal-directed AI agent that uses tools to complete tasks.\n"
            "You MUST respond with exactly one JSON object per message.\n"
            "No markdown fences, no text outside the JSON object."
        )
        steps: List[Dict] = []
        observations: List[str] = []
        all_artifacts: List[Artifact] = []

        for step_num in range(max_steps):
            # Build context with observation history (last 6)
            history_ctx = ""
            for obs in observations[-6:]:
                history_ctx += f"\nObservation: {obs}\n"

            prompt = (
                f"Task: {task}\n"
                f"{history_ctx}\n\n"
                + AGENT_PROMPT_TEMPLATE.format(manifest=manifest_json, task=task)
            )

            try:
                response = "".join(list(llm_engine.stream_tokens(
                    messages=[],
                    user_message=prompt,
                    system=system_prompt,
                    temperature=0.3,
                    max_tokens=512,
                )))
            except Exception as e:
                return AgentResult(
                    status="error",
                    result=f"LLM generation failed: {e}",
                    steps=steps,
                    total_steps=step_num,
                )

            tool_call = self._parse_tool_call(response)
            if tool_call is None:
                obs = f"[Step {step_num+1}] Could not parse action. LLM said: {response[:300]}"
                observations.append(obs)
                steps.append({"step": step_num+1, "tool": "parse_error", "observation": obs, "raw": response[:300]})
                continue

            tool_name = tool_call.tool
            params    = tool_call.params
            thought   = tool_call.thought

            # ── finish ──────────────────────────────────────────────────────
            if tool_name == "finish":
                result_text = params.get("result", "Task complete.")
                steps.append({
                    "step": step_num+1,
                    "tool": "finish",
                    "thought": thought,
                    "observation": result_text,
                    "success": True,
                })
                return AgentResult(
                    status="ok",
                    result=result_text,
                    artifacts=all_artifacts,
                    steps=steps,
                    total_steps=step_num+1,
                )

            # ── retry ────────────────────────────────────────────────────────
            if tool_name == "retry":
                reason = params.get("reason", "Unknown failure")
                obs = f"[Step {step_num+1}] Retry: {reason}. Thought: {thought}"
                observations.append(obs)
                steps.append({
                    "step": step_num+1,
                    "tool": "retry",
                    "thought": thought,
                    "observation": obs,
                    "success": False,
                })
                continue

            # ── revise ──────────────────────────────────────────────────────
            if tool_name == "revise":
                reason = params.get("reason", "Unknown failure")
                obs = f"[Step {step_num+1}] Revise: {reason}. Thought: {thought}"
                observations.append(obs)
                steps.append({
                    "step": step_num+1,
                    "tool": "revise",
                    "thought": thought,
                    "observation": obs,
                    "success": False,
                })
                continue

            # ── tool execution ──────────────────────────────────────────────
            tool_fn = self._get_tool_fn(tool_name)
            if tool_fn is None:
                obs = f"[Step {step_num+1}] Unknown tool: {tool_name}"
                observations.append(obs)
                steps.append({
                    "step": step_num+1,
                    "tool": tool_name,
                    "params": params,
                    "thought": thought,
                    "observation": obs,
                    "success": False,
                })
                continue

            obs, artifacts = self._execute_action(tool_name, params)
            observations.append(obs)
            all_artifacts.extend(artifacts)

            step_entry = {
                "step": step_num+1,
                "tool": tool_name,
                "params": params,
                "thought": thought,
                "observation": obs,
                "success": "ERROR" not in obs[:50],
                "artifacts": [a.model_dump() for a in artifacts],
            }
            steps.append(step_entry)

            # Self-correction: if tool failed, prompt LLM to revise
            if "ERROR" in obs[:50] and step_num + 1 < max_steps:
                correction_prompt = (
                    f"Tool '{tool_name}' failed with error:\n{obs}\n"
                    f"Your plan: {thought}\n"
                    f"Respond with a revised JSON action: {{'tool': '...', 'params': {{...}}, 'thought': '...'}}"
                )
                try:
                    correction = "".join(list(llm_engine.stream_tokens(
                        messages=[],
                        user_message=correction_prompt,
                        system=system_prompt,
                        temperature=0.3,
                        max_tokens=512,
                    )))
                    revised = self._parse_tool_call(correction)
                    if revised and revised.tool not in ("finish", "retry"):
                        tool_name = revised.tool
                        params    = revised.params
                        thought   = revised.thought
                        obs2, arts2 = self._execute_action(tool_name, params)
                        observations.append(obs2)
                        all_artifacts.extend(arts2)
                        steps.append({
                            "step": step_num+1.5,
                            "tool": tool_name,
                            "params": params,
                            "thought": thought + " [revised after error]",
                            "observation": obs2,
                            "success": "ERROR" not in obs2[:50],
                            "artifacts": [a.model_dump() for a in arts2],
                        })
                except Exception:
                    pass  # Keep original error, continue loop

        # Exhausted steps
        final_obs = observations[-1] if observations else "(none)"
        return AgentResult(
            status="ok",
            result=f"Agent reached max steps ({max_steps}) without finishing.\nLast: {final_obs}",
            artifacts=all_artifacts,
            steps=steps,
            total_steps=max_steps,
        )