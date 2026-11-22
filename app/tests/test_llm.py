"""LLM layer: backend resolution, Anthropic SDK call shape (SDK mocked), disk cache, refusal parsing."""
from types import SimpleNamespace

import pytest

from core import llm as llm_mod
from rag.answer import cited_numbers, extractive_answer, is_refusal
from rag.retrieval import Hit, or_query, rrf


class _FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Gallium exports were banned [1].")],
                               usage=SimpleNamespace(input_tokens=321, output_tokens=12))


def test_anthropic_backend_calls_messages_api(monkeypatch, tmp_path):
    fake = SimpleNamespace(messages=_FakeMessages())
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: fake)
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))
    from core.config import get_settings

    get_settings.cache_clear()
    try:
        m = llm_mod.AnthropicLLM("claude-haiku-5-5", "sk-test")
        r1 = m.complete("sys", "question?", 100)
        r2 = m.complete("sys", "question?", 100)  # served from disk cache
    finally:
        get_settings.cache_clear()
    call = fake.messages.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["system"] == "sys" and call["max_tokens"] == 100
    assert call["messages"] == [{"role": "user", "content": "question?"}]
    assert r1.text.startswith("Gallium") and r1.input_tokens == 321 and not r1.cached
    assert r2.cached and r2.input_tokens == 321 and len(fake.messages.calls) == 1


def test_backend_resolution(monkeypatch):
    from core.config import get_settings

    for env, which, expected in [({"LLM_BACKEND": "auto", "ANTHROPIC_API_KEY": "k"}, None, "anthropic"),
                                 ({"LLM_BACKEND": "auto", "ANTHROPIC_API_KEY": ""}, None, "none"),
                                 ({"LLM_BACKEND": "auto", "ANTHROPIC_API_KEY": ""}, "/usr/bin/claude", "claude_cli"),
                                 ({"LLM_BACKEND": "fake"}, None, "fake")]:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(llm_mod.shutil, "which", lambda _: which)
        get_settings.cache_clear()
        assert llm_mod.resolve_backend() == expected
    get_settings.cache_clear()
    assert llm_mod.get_text_llm(backend="none") is None


def test_empty_secrets_fall_back_to_dev_defaults(monkeypatch):
    from core.config import DEV_DEFAULTS, get_settings

    monkeypatch.setenv("WEBHOOK_SIGNING_SECRET", "")
    monkeypatch.setenv("JWT_SECRET", "")
    get_settings.cache_clear()
    s = get_settings()
    assert s.webhook_signing_secret == DEV_DEFAULTS["webhook_signing_secret"] and s.jwt_secret == DEV_DEFAULTS["jwt_secret"]
    get_settings.cache_clear()


def _hit(i, doc, text="t"):
    return Hit(chunk_id=i, document_id=doc, title=doc, source="s", source_url="u", synthetic=False, text=text,
               similarity=0.8, score=0.0)


def test_rrf_fuses_rankings():
    a = [_hit(1, "a"), _hit(2, "b"), _hit(3, "c")]
    b = [_hit(3, "c"), _hit(2, "b")]
    fused = rrf([a, b])
    assert [h.document_id for h in fused][:2] == ["b", "c"]  # in both lists -> ahead of 'a'


def test_helpers():
    assert or_query("Who is the CEO of Kivu?") == "who | the | ceo | kivu"
    assert is_refusal("I don't know based on the available documents.") and not is_refusal("Gallium [1]")
    assert cited_numbers("x [2] y [1] z [2]") == [1, 2]
    hits = [_hit(1, "g", "Title\nChina banned gallium exports in December 2024. Other text here.")]
    assert "[1]" in extractive_answer("When were gallium exports banned?", hits)
