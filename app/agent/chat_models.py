"""Chat models for the LangGraph agent.

* ChatAnthropic (langchain-anthropic) when ANTHROPIC_API_KEY is set: native tool use.
* ChatClaudeCLI: wraps `claude -p` (no API key on the build host). It has no
  native tool-calling here, so it uses a JSON protocol: the model replies
  {"tool_calls": [{"name": ..., "args": {...}}]} or {"final": "..."}, which we
  turn into a normal AIMessage with `tool_calls`, so the graph is identical.
* ScriptedChatModel: returns pre-scripted AIMessages (tests).
* KeywordRouterModel: deterministic keyword router (LLM_BACKEND=fake demo mode).
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

from core.config import get_settings
from core.llm import ClaudeCLILLM, resolve_backend


class _ToolBindingMixin:
    _tools: list[dict] = []

    def bind_tools(self, tools: Sequence[Any], **kwargs):  # noqa: D401
        clone = self.model_copy()
        clone._tools = [convert_to_openai_tool(t)["function"] for t in tools]
        return clone


PROTOCOL = """You can call tools. Available tools (JSON schema):
{tools}

Reply with ONLY one JSON object, no prose, no markdown fences:
  {{"tool_calls": [{{"name": "<tool>", "args": {{...}}}}]}}   to call one or more tools, or
  {{"final": "<answer for the user>"}}                          when you have enough information.
Call each tool at most once with the same arguments. Base the final answer only on tool results."""


def _render_transcript(messages: list[BaseMessage]) -> tuple[str, str]:
    system = ""
    lines = []
    for m in messages:
        if isinstance(m, SystemMessage):
            system += str(m.content) + "\n"
        elif isinstance(m, HumanMessage):
            lines.append(f"USER: {m.content}")
        elif isinstance(m, AIMessage):
            if m.tool_calls:
                lines.append("ASSISTANT: " + json.dumps({"tool_calls": [{"name": t["name"], "args": t["args"]}
                                                                        for t in m.tool_calls]}))
            elif m.content:
                lines.append("ASSISTANT: " + json.dumps({"final": m.content}))
        elif isinstance(m, ToolMessage):
            content = str(m.content)
            lines.append(f"TOOL RESULT ({m.name}): {content[:6000]}")
    return system, "\n\n".join(lines)


def parse_protocol(text: str) -> AIMessage:
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        obj = json.loads(t[t.index("{"): t.rindex("}") + 1])
    except ValueError:
        return AIMessage(content=text.strip())
    if obj.get("tool_calls"):
        calls = [{"name": c["name"], "args": c.get("args", {}), "id": f"call_{uuid.uuid4().hex[:10]}",
                  "type": "tool_call"} for c in obj["tool_calls"]]
        return AIMessage(content="", tool_calls=calls)
    return AIMessage(content=str(obj.get("final", text)).strip())


class ChatClaudeCLI(_ToolBindingMixin, BaseChatModel):
    model: str = "claude-sonnet-5-5"
    _tools: list[dict] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "claude-cli"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        system, transcript = _render_transcript(messages)
        system = system + "\n" + PROTOCOL.format(tools=json.dumps(self._tools, indent=1))
        res = ClaudeCLILLM(self.model).complete(system, transcript + "\n\nASSISTANT:", 1500)
        msg = parse_protocol(res.text)
        msg.usage_metadata = {"input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                              "total_tokens": res.input_tokens + res.output_tokens}
        return ChatResult(generations=[ChatGeneration(message=msg)])


class ScriptedChatModel(_ToolBindingMixin, BaseChatModel):
    """Returns the scripted responses in order (tests). Records what it saw."""

    responses: list[AIMessage]
    _i: int = PrivateAttr(default=0)
    _tools: list[dict] = PrivateAttr(default_factory=list)
    seen: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        # keep shared state (counter) - return self
        self._tools = [convert_to_openai_tool(t)["function"] for t in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        msg = self.responses[min(self._i, len(self.responses) - 1)]
        self._i += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


MATERIAL_WORDS = ["lithium", "cobalt", "nickel", "rare earth", "gallium", "germanium", "graphite", "tungsten",
                  "antimony", "manganese"]


class KeywordRouterModel(_ToolBindingMixin, BaseChatModel):
    """No-LLM router used when LLM_BACKEND=fake: picks one tool from keywords,
    then summarises the tool result. Lets the agent endpoint work offline."""

    _tools: list[dict] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "keyword-router"

    @staticmethod
    def route(question: str) -> tuple[str, dict]:
        q = question.lower()
        mat = next((m for m in MATERIAL_WORDS if m in q), None)
        mat_arg = "Rare Earths" if mat == "rare earth" else (mat.title() if mat else None)
        if "report" in q and mat_arg:
            return "write_risk_report", {"material": mat_arg}
        if any(w in q for w in ("supplier", "exposure", "exposed", "who supplies", "acme")):
            comp = re.search(r"([A-Z][a-z]+(?: [A-Z][a-z]+)+)", question)
            return "supplier_exposure", ({"material": mat_arg} if mat_arg else
                                         {"company": comp.group(1) if comp else question})
        if any(w in q for w in ("history", "trend", "last 30", "past month", "over time")) and mat_arg:
            return "get_material_history", {"material": mat_arg}
        if any(w in q for w in ("score", "signal", "risk level", "current risk", "how risky")) and mat_arg:
            return "get_material_signal", {"material": mat_arg}
        return "search_documents", {"query": question}

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        last = messages[-1]
        if isinstance(last, ToolMessage):
            msg = AIMessage(content=f"Based on {last.name}: {str(last.content)[:700]}")
        else:
            q = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
            name, args = self.route(str(q))
            msg = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}",
                                                     "type": "tool_call"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


def get_agent_chat_model(backend: str | None = None) -> BaseChatModel:
    s = get_settings()
    b = resolve_backend(backend)
    if b == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=s.agent_model, api_key=s.anthropic_api_key, max_tokens=1500, temperature=0)
    if b == "claude_cli":
        return ChatClaudeCLI(model=s.agent_model)
    return KeywordRouterModel()
