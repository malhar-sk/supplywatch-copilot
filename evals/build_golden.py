"""Build evals/golden.jsonl (50 items). Run once on the host; output is committed.

  factual (22)      one doc each; question + reference drafted by an LLM (Haiku-class) FROM that doc
  multi_doc (8)     two docs each; drafted by the LLM from both
  exposure (10)     computed exactly from data/suppliers.json (fictional), no LLM
  not_in_corpus (10) hand-written; each checked by grep to have no answer in data/corpus

expected_docs: the source doc(s). For USGS docs, the other year's edition of the
same section is added when it contains the reference answer's key number, so
retrieving either edition counts as a hit.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from core.llm import get_text_llm  # noqa: E402
from rag.ingest import parse_doc  # noqa: E402

CORPUS = ROOT / "data" / "corpus"
OUT = ROOT / "evals" / "golden.jsonl"

FACTUAL = [
    "usgs/usgs-2025-gallium-events-trends-and-issues.md",
    "usgs/usgs-2025-germanium-events-trends-and-issues.md",
    "usgs/usgs-2025-antimony-events-trends-and-issues.md",
    "usgs/usgs-2025-graphite-events-trends-and-issues.md",
    "usgs/usgs-2025-lithium-world-production-and-reserves.md",
    "usgs/usgs-2025-cobalt-world-production-and-reserves.md",
    "usgs/usgs-2025-nickel-events-trends-and-issues.md",
    "usgs/usgs-2025-rare-earths-domestic-production-and-use.md",
    "usgs/usgs-2025-tungsten-domestic-production-and-use.md",
    "usgs/usgs-2025-manganese-substitutes.md",
    "usgs/usgs-2025-lithium-substitutes.md",
    "usgs/usgs-2025-cobalt-recycling-and-import-sources.md",
    "usgs/usgs-2025-tungsten-recycling-and-import-sources.md",
    "usgs/usgs-2025-graphite-world-resources.md",
    "usgs/usgs-2025-manganese-domestic-production-and-use.md",
    "usgs/usgs-2024-rare-earths-events-trends-and-issues.md",
    "usgs/usgs-2024-cobalt-government-stockpile.md",
    "usgs/usgs-2025-germanium-domestic-production-and-use.md",
    "usgs/usgs-2025-antimony-substitutes.md",
    "synthetic/news-synth-00-00.md",
    "synthetic/news-synth-03-01.md",
    "synthetic/news-synth-05-02.md",
]
MULTI = [
    ("usgs/usgs-2025-gallium-events-trends-and-issues.md", "usgs/usgs-2025-germanium-events-trends-and-issues.md"),
    ("usgs/usgs-2025-lithium-world-production-and-reserves.md", "usgs/usgs-2025-cobalt-world-production-and-reserves.md"),
    ("usgs/usgs-2024-antimony-events-trends-and-issues.md", "usgs/usgs-2025-antimony-events-trends-and-issues.md"),
    ("usgs/usgs-2025-graphite-substitutes.md", "usgs/usgs-2025-lithium-substitutes.md"),
    ("usgs/usgs-2025-tungsten-events-trends-and-issues.md", "usgs/usgs-2025-antimony-events-trends-and-issues.md"),
    ("usgs/usgs-2025-nickel-world-production-and-reserves.md", "usgs/usgs-2025-manganese-world-production-and-reserves.md"),
    ("synthetic/supplier-lumen-semiconductor-substrates.md", "usgs/usgs-2025-gallium-events-trends-and-issues.md"),
    ("synthetic/supplier-verdant-anode-co.md", "usgs/usgs-2025-graphite-events-trends-and-issues.md"),
]
NOT_IN_CORPUS = [
    "Which country is the world's largest producer of palladium?",
    "How many employees does Halvorsen Battery Materials have?",
    "Who is the CEO of Kivu Cobalt Logistics?",
    "What is the melting point of tungsten in degrees Celsius?",
    "How much does it cost to replace a Tesla Model 3 battery pack?",
    "What is the half-life of cobalt-60?",
    "What percentage of global silver production comes from Mexico?",
    "What is the U.S. federal tax credit for buying a new electric vehicle?",
    "What interest rate did the Federal Reserve set in its last meeting?",
    "How much did lithium lobbying groups spend in Washington in 2024?",
]

ONE = """Passage title: {title}
Passage:
{body}

Write ONE specific question a supply-chain risk analyst might ask that this passage answers, and a
1-2 sentence reference answer using ONLY facts in the passage. The question must be self-contained:
name the material, and the company if it is about a company; do not say "the passage" or "the
document". Prefer questions about export controls, production shares, reserves, prices, import
sources, substitutes or uses. Return ONLY JSON: {{"question": "...", "reference": "..."}}"""

TWO = """Passage A title: {t1}
Passage A:
{b1}

Passage B title: {t2}
Passage B:
{b2}

Write ONE question that needs BOTH passages to answer (e.g. a comparison or a combination), and a
2-3 sentence reference answer using ONLY these passages. Self-contained: name the materials/companies;
never say "passage". Return ONLY JSON: {{"question": "...", "reference": "..."}}"""


def load(rel: str) -> tuple[dict, str]:
    return parse_doc((CORPUS / rel).read_text(encoding="utf-8"))


def twins(doc_id: str, reference: str) -> list[str]:
    m = re.match(r"usgs-(2024|2025)-(.+)$", doc_id)
    if not m:
        return []
    other = f"usgs-{'2024' if m.group(1) == '2025' else '2025'}-{m.group(2)}"
    path = CORPUS / "usgs" / f"{other}.md"
    if not path.exists():
        return []
    body = path.read_text(encoding="utf-8")
    nums = [n for n in re.findall(r"\d[\d,.]*\d|\d", reference) if len(n) >= 2 and not re.fullmatch(r"202[3-6]", n)]
    return [other] if nums and all(n in body for n in nums) else []


def llm_json(llm, prompt: str) -> dict:
    t = llm.complete("You write evaluation data. Output JSON only.", prompt, 600).text
    return json.loads(t[t.index("{"): t.rindex("}") + 1])


def exposure_items() -> list[dict]:
    data = json.loads((ROOT / "data" / "suppliers.json").read_text(encoding="utf-8"))
    sup = {s["company"]: s for s in data["suppliers"]}
    slug = lambda c: "supplier-" + re.sub(r"[^a-z0-9]+", "-", c.lower()).strip("-")  # noqa: E731
    items = []
    for c in ["Kivu Cobalt Logistics", "Lumen Semiconductor Substrates", "Saffron Flame Retardants",
              "Bellmont Tungsten Tools", "Verdant Anode Co."]:
        s = sup[c]
        rows = "; ".join(f"{m['material']} {m['share_pct']}% from {m['country']}" for m in s["materials"])
        items.append({"question": f"Which countries does {c} source its materials from, and in what shares?",
                      "reference": f"{c} (fictional, HQ {s['hq']}) sources: {rows}.", "expected_docs": [slug(c)]})
    bom = {p["product"]: p["suppliers"] for p in data["customer"]["products"]}
    for prod in ["48V battery pack", "EV traction inverter", "Industrial cutting tool line"]:
        items.append({"question": f"Which suppliers does Acme Drives use for its {prod}?",
                      "reference": f"Acme Drives' {prod} is supplied by {', '.join(bom[prod])}".rstrip('.') + ".",
                      "expected_docs": ["customer-acme-drives-bom"]})
    # material+country exposure
    for mat, ctry in [("Cobalt", "DRC"), ("Gallium", "China")]:
        hits = [(s["company"], m["share_pct"]) for s in data["suppliers"] for m in s["materials"]
                if m["material"] == mat and m["country"] == ctry]
        items.append({"question": f"Which suppliers in the directory source {mat.lower()} from {ctry}?",
                      "reference": "; ".join(f"{c} ({p}% of its {mat.lower()})" for c, p in hits) + ".",
                      "expected_docs": [slug(c) for c, _ in hits]})
    return items


# Hand edits after reviewing the LLM drafts against the source text (wording only;
# facts verified in the docs). Applied by question id.
OVERRIDES = {
    "q04": {"question": "What share of world graphite production did China account for in 2024, and what share "
                        "of China's graphite output was flake versus amorphous?"},
    "q07": {"question": "In May 2024, what grant did the U.S. Department of Defense award under the Defense "
                        "Production Act Title III for a cobalt and nickel hydrometallurgical demonstration plant, "
                        "and where is the recipient company based?",
            "reference": "The Department of Defense awarded a $7 million grant to a Missouri company to develop a "
                         "hydrometallurgical demonstration plant producing cobalt and nickel products from a variety "
                         "of feedstocks."},
}


def main() -> None:
    llm = get_text_llm(model="claude-haiku-5-5")
    items: list[dict] = []
    for rel in FACTUAL:
        meta, body = load(rel)
        qa = llm_json(llm, ONE.format(title=meta["title"], body=body[:3500]))
        items.append({"type": "factual", **qa, "expected_docs": [meta["id"]] + twins(meta["id"], qa["reference"])})
    for a, b in MULTI:
        (m1, b1), (m2, b2) = load(a), load(b)
        qa = llm_json(llm, TWO.format(t1=m1["title"], b1=b1[:2500], t2=m2["title"], b2=b2[:2500]))
        items.append({"type": "multi_doc", **qa, "expected_docs": [m1["id"], m2["id"]]})
    items += [{"type": "exposure", **x} for x in exposure_items()]
    items += [{"type": "not_in_corpus", "question": q, "reference": "", "expected_docs": []} for q in NOT_IN_CORPUS]
    with OUT.open("w", encoding="utf-8") as f:
        for i, it in enumerate(items, 1):
            qid = f"q{i:02d}"
            it.update(OVERRIDES.get(qid, {}))
            f.write(json.dumps({"id": qid, **it}, ensure_ascii=False) + "\n")
    print(f"wrote {len(items)} items to {OUT}")


if __name__ == "__main__":
    main()
