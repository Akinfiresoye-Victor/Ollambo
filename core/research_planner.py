"""
core/research_planner.py - Research plan generator.

One model call (with JSON retry fallback, same pattern as planner.py) to
produce a structured research plan: main question, 3-7 sub-questions,
per-SQ queries/evidence criteria, and a skeptic checklist.
No UI imports.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from core.ollama_client import OllamaClient
from core.prompts import RESEARCH_PLAN_SYSTEM, RESEARCH_PLAN_JSON_RETRY

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SubQuestion:
    index: int
    question: str
    why_it_matters: str
    search_queries: list[str]
    evidence_for: str       # what would support the answer
    evidence_against: str   # what would disprove it
    preferred_sources: list[str]
    completed: bool = False


@dataclass
class ResearchPlan:
    topic: str
    main_question: str
    sub_questions: list[SubQuestion] = field(default_factory=list)
    skeptic_checklist: dict[str, Any] = field(default_factory=dict)
    raw_json: str = ""
    project_name: str = ""

    def to_dict(self) -> dict:
        d = {
            "topic": self.topic,
            "project_name": self.project_name,
            "main_question": self.main_question,
            "sub_questions": [
                {
                    "index": sq.index,
                    "question": sq.question,
                    "why_it_matters": sq.why_it_matters,
                    "search_queries": sq.search_queries,
                    "evidence_for": sq.evidence_for,
                    "evidence_against": sq.evidence_against,
                    "preferred_sources": sq.preferred_sources,
                    "completed": sq.completed,
                }
                for sq in self.sub_questions
            ],
            "skeptic_checklist": self.skeptic_checklist,
        }
        return d

    @staticmethod
    def from_dict(d: dict) -> "ResearchPlan":
        sqs = [
            SubQuestion(
                index=sq.get("index", i + 1),
                question=sq.get("question", ""),
                why_it_matters=sq.get("why_it_matters", ""),
                search_queries=sq.get("search_queries", []),
                evidence_for=sq.get("evidence_for", ""),
                evidence_against=sq.get("evidence_against", ""),
                preferred_sources=sq.get("preferred_sources", []),
                completed=sq.get("completed", False),
            )
            for i, sq in enumerate(d.get("sub_questions", []))
        ]
        return ResearchPlan(
            topic=d.get("topic", ""),
            main_question=d.get("main_question", ""),
            sub_questions=sqs,
            skeptic_checklist=d.get("skeptic_checklist", {}),
            project_name=d.get("project_name", ""),
        )


# ---------------------------------------------------------------------------
# JSON Schema for the plan
# ---------------------------------------------------------------------------

_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_name": {"type": "string"},
        "main_question": {"type": "string"},
        "sub_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "question": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "search_queries": {"type": "array", "items": {"type": "string"}},
                    "evidence_for": {"type": "string"},
                    "evidence_against": {"type": "string"},
                    "preferred_sources": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["index", "question", "search_queries"],
            },
        },
        "skeptic_checklist": {
            "type": "object",
            "properties": {
                "myths": {"type": "array", "items": {"type": "string"}},
                "biases": {"type": "array", "items": {"type": "string"}},
                "claims_needing_proof": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "required": ["main_question", "sub_questions"],
}


# ---------------------------------------------------------------------------
# Parse helper
# ---------------------------------------------------------------------------

def parse_plan_json(text: str, topic: str) -> ResearchPlan | None:
    cleaned = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1).strip()
    try:
        data = json.loads(cleaned)
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(cleaned[start:end + 1])
            except Exception:
                return None
        else:
            return None

    if not isinstance(data, dict):
        return None

    raw_sqs = data.get("sub_questions", [])
    if not isinstance(raw_sqs, list) or not raw_sqs:
        return None

    sqs = []
    for i, sq in enumerate(raw_sqs, start=1):
        if not isinstance(sq, dict):
            continue
        sqs.append(SubQuestion(
            index=int(sq.get("index", i)),
            question=str(sq.get("question", "")),
            why_it_matters=str(sq.get("why_it_matters", "")),
            search_queries=[str(q) for q in sq.get("search_queries", [])],
            evidence_for=str(sq.get("evidence_for", "")),
            evidence_against=str(sq.get("evidence_against", "")),
            preferred_sources=[str(s) for s in sq.get("preferred_sources", [])],
        ))

    pname = str(data.get("project_name", "")).strip()

    return ResearchPlan(
        topic=topic,
        main_question=str(data.get("main_question", topic)),
        sub_questions=sqs,
        skeptic_checklist=data.get("skeptic_checklist", {}),
        raw_json=text,
        project_name=pname,
    )


def generate_continuation_subquestions(
    existing_plan: ResearchPlan,
    ledger: list[dict],
    follow_up_prompt: str,
    ollama_url: str,
    model: str,
    num_ctx: int = 8192,
    client: OllamaClient | None = None,
) -> list[SubQuestion]:
    """Generate 2 to 3 extra sub-questions for continuing research on an existing topic."""
    old_total = len(existing_plan.sub_questions)
    claims_digest = "\n".join(
        f"- {item.get('claim', '')} (Confidence: {item.get('confidence', 0)}%)"
        for item in ledger[:8]
    ) or "(No ledger claims recorded yet)"

    system_prompt = (
        "You are a research director expanding an existing research investigation. "
        "The user wants to follow up or dig deeper into specific aspects.\n\n"
        f"Existing Topic: {existing_plan.topic}\n"
        f"Main Question: {existing_plan.main_question}\n\n"
        f"Known Findings So Far:\n{claims_digest}\n\n"
        "Generate exactly 2 to 3 NEW, highly targeted sub-questions addressing the follow-up request. "
        "Do not repeat existing questions. Return JSON matching the sub_questions schema."
    )
    user_prompt = f"Follow-up request: {follow_up_prompt}"

    own_client = False
    if client is None:
        client = OllamaClient(ollama_url)
        own_client = True

    try:
        from core.storage import get_configured_threads
        gen_opts: dict[str, Any] = {
            "num_ctx": num_ctx,
            "temperature": 0.2,
            "top_p": 0.9,
        }
        threads = get_configured_threads()
        if threads is not None:
            gen_opts["num_thread"] = threads

        resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options=gen_opts,
            format_schema={
                "type": "object",
                "properties": {
                    "sub_questions": _PLAN_SCHEMA["properties"]["sub_questions"]
                },
                "required": ["sub_questions"],
            },
        )
        content = resp.get("message", {}).get("content", "") if isinstance(resp, dict) else str(resp)
        parsed = parse_plan_json(content, existing_plan.topic)
        if parsed and parsed.sub_questions:
            extra = parsed.sub_questions[:3]
            for idx, sq in enumerate(extra, start=old_total + 1):
                sq.index = idx
            return extra
    except Exception:
        pass
    finally:
        if own_client:
            client.close()

    return [
        SubQuestion(
            index=old_total + 1,
            question=f"Investigate: {follow_up_prompt}",
            why_it_matters="Direct user follow-up inquiry",
            search_queries=[f"{existing_plan.topic} {follow_up_prompt}", f"{follow_up_prompt} analysis"],
            evidence_for="Supporting findings",
            evidence_against="Counter evidence",
            preferred_sources=["credible sources"],
        ),
        SubQuestion(
            index=old_total + 2,
            question=f"What are the implications or opposing views regarding {follow_up_prompt}?",
            why_it_matters="Comprehensive verification",
            search_queries=[f"{follow_up_prompt} controversy", f"{follow_up_prompt} critique"],
            evidence_for="Supporting arguments",
            evidence_against="Critical data",
            preferred_sources=["expert analysis"],
        ),
    ]


# ---------------------------------------------------------------------------
# Plan generator
# ---------------------------------------------------------------------------

def generate_research_plan(
    topic: str,
    ollama_url: str,
    model: str,
    num_ctx: int = 8192,
    num_sq: int = 5,
    stop_flag: Callable[[], bool] | None = None,
    client: OllamaClient | None = None,
) -> ResearchPlan:
    """Call the model to produce a ResearchPlan. Uses JSON schema + retry fallback."""
    from core.storage import get_configured_threads

    own_client = False
    if client is None:
        client = OllamaClient(ollama_url)
        own_client = True

    opts: dict[str, Any] = {
        "num_ctx": num_ctx,
        "temperature": 0.3,
        "top_p": 0.9,
        "num_predict": 1200,
    }
    threads = get_configured_threads()
    if threads is not None:
        opts["num_thread"] = threads

    user_prompt = (
        f"Topic: {topic}\n\nCreate a research plan with exactly {num_sq} sub-questions."
    )
    messages = [
        {"role": "system", "content": RESEARCH_PLAN_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    def _collect(msgs: list[dict], schema: Any) -> str:
        chunks: list[str] = []
        for chunk in client.chat_stream(
            model=model,
            messages=msgs,
            options=opts,
            format_schema=schema,
            stop_flag=stop_flag,
        ):
            if stop_flag and stop_flag():
                break
            content = chunk.get("message", {}).get("content", "")
            if content:
                chunks.append(content)
            if chunk.get("done"):
                break
        return "".join(chunks).strip()

    try:
        output = _collect(messages, _PLAN_SCHEMA)
        plan = parse_plan_json(output, topic)
        if plan and plan.sub_questions:
            return plan

        # Retry
        messages.append({"role": "assistant", "content": output})
        messages.append({"role": "user", "content": RESEARCH_PLAN_JSON_RETRY})
        retry = _collect(messages, "json")
        plan = parse_plan_json(retry, topic)
        if plan and plan.sub_questions:
            return plan

        # Fallback: generic plan
        return _fallback_plan(topic, num_sq)
    finally:
        if own_client:
            client.close()


def _fallback_plan(topic: str, num_sq: int) -> ResearchPlan:
    """Return a sensible generic plan when parsing fails."""
    sqs = [
        SubQuestion(
            index=1,
            question=f"What is {topic} and how does it work?",
            why_it_matters="Establishes foundational understanding",
            search_queries=[f"{topic} overview", f"what is {topic}"],
            evidence_for="Clear definition and mechanism description",
            evidence_against="Contradictory definitions",
            preferred_sources=["encyclopedias", "official documentation"],
        ),
        SubQuestion(
            index=2,
            question=f"What evidence supports claims about {topic}?",
            why_it_matters="Tests factual basis of common claims",
            search_queries=[f"{topic} evidence", f"{topic} research studies"],
            evidence_for="Peer-reviewed studies, primary data",
            evidence_against="Lack of replication, small samples",
            preferred_sources=["academic papers", "government data"],
        ),
        SubQuestion(
            index=3,
            question=f"What are the criticisms or failure cases of {topic}?",
            why_it_matters="Provides balanced view",
            search_queries=[f"{topic} criticism", f"{topic} problems debunked"],
            evidence_for="Documented failures or criticism from experts",
            evidence_against="Rebuttals from credible sources",
            preferred_sources=["investigative journalism", "academic critique"],
        ),
    ]
    for i in range(4, num_sq + 1):
        sqs.append(SubQuestion(
            index=i,
            question=f"Sub-question {i} about {topic}",
            why_it_matters="Further investigation needed",
            search_queries=[f"{topic} details {i}"],
            evidence_for="Supporting data",
            evidence_against="Counter-evidence",
            preferred_sources=["reputable sources"],
        ))
    return ResearchPlan(
        topic=topic,
        main_question=f"What do we know about {topic}, critically examined?",
        sub_questions=sqs[:num_sq],
        skeptic_checklist={
            "myths": [f"Common myths about {topic}"],
            "biases": ["Commercial interest", "Confirmation bias"],
            "claims_needing_proof": [f"Main claims about {topic}"],
        },
    )
