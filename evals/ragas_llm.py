"""Adapters so RAGAS can use our LLM layer (claude CLI on the build host, the
Anthropic API elsewhere) as the judge, and our local bge-small model as the
embedding model. Counts judge tokens."""
from __future__ import annotations

import threading

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from core.llm import get_text_llm
from rag.embeddings import embed_passages

JUDGE_SYSTEM = "You are a careful evaluation assistant. Follow the output format instructions exactly."


class TokenCounter:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = self.cached = self.input_tokens = self.output_tokens = 0

    def add(self, res):
        with self.lock:
            self.calls += 1
            self.cached += int(res.cached)
            self.input_tokens += res.input_tokens
            self.output_tokens += res.output_tokens

    def as_dict(self):
        return {"judge_calls": self.calls, "judge_cached_calls": self.cached,
                "judge_input_tokens": self.input_tokens, "judge_output_tokens": self.output_tokens}


class JudgeChat(BaseChatModel):
    model: str = "claude-haiku-5-5"
    backend: str | None = None
    counter: TokenCounter

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "supplywatch-judge"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        system = "\n".join(str(m.content) for m in messages if isinstance(m, SystemMessage)) or JUDGE_SYSTEM
        prompt = "\n\n".join(str(m.content) for m in messages if not isinstance(m, SystemMessage))
        llm = get_text_llm(backend=self.backend, model=self.model)
        res = llm.complete(system, prompt, 2000)
        self.counter.add(res)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=res.text))])


class LocalEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return embed_passages(texts)

    def embed_query(self, text):
        # RAGAS compares question<->generated-question similarity: symmetric, no query instruction
        return embed_passages([text])[0]
