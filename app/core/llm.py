"""Small LLM abstraction used by /ask, the corpus builder and the evals.

Backends (settings.llm_backend / LLM_BACKEND):
  anthropic   Anthropic Messages API via the official SDK (needs ANTHROPIC_API_KEY)
  claude_cli  shells out to a logged-in `claude -p` (used on the build host, which
              has a Claude Code login but no API key)
  fake        deterministic canned output, for tests and CI (no network)
  none        no LLM at all; callers fall back to an extractive answer
  auto        anthropic if a key is set, else claude_cli if `claude` is on PATH, else none

Every non-fake response is cached on disk (settings.llm_cache_dir), keyed by
backend+model+system+prompt, so re-running evals costs nothing. Cached results
keep the token counts of the original call and are flagged `cached=True`.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from core.config import get_settings


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    cached: bool = False
    backend: str = ""


class TextLLM:
    backend = "base"

    def __init__(self, model: str):
        self.model = model

    def _call(self, system: str, prompt: str, max_tokens: int) -> LLMResult:  # pragma: no cover
        raise NotImplementedError

    def complete(self, system: str, prompt: str, max_tokens: int = 800) -> LLMResult:
        cache = _cache_path(self.backend, self.model, system, prompt, max_tokens)
        if cache and cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            data["cached"] = True
            data["latency_s"] = 0.0
            return LLMResult(**data)
        t0 = time.perf_counter()
        res = self._call(system, prompt, max_tokens)
        res.latency_s = time.perf_counter() - t0
        res.backend = self.backend
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(asdict(res)), encoding="utf-8")
        return res


def _cache_path(backend: str, model: str, system: str, prompt: str, max_tokens: int) -> Path | None:
    if backend in ("fake", "none"):
        return None
    d = get_settings().llm_cache_dir
    if not d:
        return None
    key = hashlib.sha256(json.dumps([backend, model, system, prompt, max_tokens]).encode()).hexdigest()
    return Path(d) / key[:2] / f"{key}.json"


class AnthropicLLM(TextLLM):
    backend = "anthropic"

    def __init__(self, model: str, api_key: str):
        super().__init__(model)
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key, timeout=90, max_retries=3)

    def _call(self, system, prompt, max_tokens):
        msg = self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                          messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return LLMResult(text=text, model=self.model, input_tokens=msg.usage.input_tokens,
                         output_tokens=msg.usage.output_tokens)


_cli_lock = threading.Semaphore(int(os.environ.get("CLAUDE_CLI_CONCURRENCY", "3")))


class ClaudeCLILLM(TextLLM):
    """`claude -p` with tools, MCP servers and settings disabled: a plain completion."""

    backend = "claude_cli"

    def _call(self, system, prompt, max_tokens):
        cmd = ["claude", "-p", "--model", self.model, "--output-format", "json",
               "--system-prompt", system, "--tools", "", "--no-session-persistence",
               "--strict-mcp-config", "--setting-sources", ""]
        last_err = ""
        for attempt in range(3):
            with _cli_lock, tempfile.TemporaryDirectory() as tmp:
                p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=tmp, timeout=240)
            if p.returncode == 0:
                try:
                    data = json.loads(p.stdout)
                except json.JSONDecodeError:
                    last_err = p.stdout[-300:]
                else:
                    if not data.get("is_error"):
                        u = data.get("usage", {})
                        inp = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                        return LLMResult(text=data.get("result", ""), model=self.model, input_tokens=inp,
                                         output_tokens=u.get("output_tokens", 0))
                    last_err = str(data.get("result", ""))[:300]
            else:
                last_err = (p.stderr or p.stdout)[-300:]
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"claude CLI failed: {last_err}")


class FakeLLM(TextLLM):
    """Deterministic stand-in. Answers with the first sentence of context [1]
    and cites it, or refuses when the prompt has no context block."""

    backend = "fake"

    def _call(self, system, prompt, max_tokens):
        if "[1]" not in prompt:
            text = "I don't know based on the available documents."
        else:
            seg = prompt.split("[1]", 1)[1].split("\n", 2)
            first = seg[1] if len(seg) > 1 else seg[0]
            text = f"{first.strip()[:300]} [1]"
        return LLMResult(text=text, model="fake", input_tokens=len(prompt) // 4, output_tokens=len(text) // 4)


def resolve_backend(backend: str | None = None) -> str:
    s = get_settings()
    b = (backend or s.llm_backend or "auto").lower()
    if b == "auto":
        if s.anthropic_api_key:
            return "anthropic"
        if shutil.which("claude"):
            return "claude_cli"
        return "none"
    return b


def get_text_llm(backend: str | None = None, model: str | None = None) -> TextLLM | None:
    """Returns None for the `none` backend (callers use an extractive fallback)."""
    s = get_settings()
    b = resolve_backend(backend)
    model = model or s.answer_model
    if b == "anthropic":
        if not s.anthropic_api_key:
            raise RuntimeError("LLM_BACKEND=anthropic but ANTHROPIC_API_KEY is empty")
        return AnthropicLLM(model, s.anthropic_api_key)
    if b == "claude_cli":
        return ClaudeCLILLM(model)
    if b == "fake":
        return FakeLLM(model)
    return None
