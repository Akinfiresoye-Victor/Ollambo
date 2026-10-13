"""
core/research_writer.py - Section-by-section research report generator and post-check validator.

Generates the 11-section structured Markdown report:
1. Title and TL;DR in plain English (3-5 short sentences)
2. Key findings: citation numbers [1][3], confidence label (High, Medium, Low) with reason
3. What is disputed or uncertain: contested claims shown side by side
4. Myths and misconceptions checked: from skeptic checklist
5. How it works: simple explanation + Mermaid diagram
6. Glossary: technical/uncommon words explained in one sentence
7. Explain it like I'm 12: plain explanation with real-life analogy
8. Test yourself: 5 quiz questions with answers hidden (<details><summary>)
9. What we still don't know & suggested next questions
10. How this research was done (deterministic methodology stats)
11. Sources table (deterministic Markdown table)

Post-checks:
- Citation ID validation against sources list
- Findings citation completeness check
- Mermaid diagram syntax validation + single repair call + fallback
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Callable

from core.ollama_client import OllamaClient
from core.prompts import (
    RESEARCH_SECTION_SYSTEM,
    RESEARCH_SECTION_USER,
    RESEARCH_MERMAID_REPAIR_SYSTEM,
    RESEARCH_MERMAID_REPAIR_USER,
)
from core.research_budget import sq_num_predict, trim_text_to_budget, input_budget
from core.research_store import save_report, save_status, update_index_entry


# ---------------------------------------------------------------------------
# Post-Check: Citation Validation (Plain Python)
# ---------------------------------------------------------------------------

def validate_citations(markdown: str, valid_source_ids: set[int]) -> tuple[str, list[int]]:
    """
    Find citation numbers in [1], [2], [1][3] etc.
    Remove or flag any citation number that does not exist in valid_source_ids.
    Returns (cleaned_markdown, invalid_ids_found).
    """
    invalid_found: list[int] = []

    def _replace_citation(match: re.Match) -> str:
        cid_str = match.group(1)
        try:
            cid = int(cid_str)
            if cid in valid_source_ids:
                return match.group(0)
            invalid_found.append(cid)
            return ""  # remove invalid citation number
        except ValueError:
            return match.group(0)

    # Replace bracketed numbers like [4] but not links like [text](url)
    cleaned = re.sub(r"\[(\d+)\](?!\()", _replace_citation, markdown)
    # Clean up empty brackets or leftover artifacts
    cleaned = re.sub(r"\[\s*\]", "", cleaned)
    return cleaned, invalid_found


def check_uncredited_findings(findings_text: str) -> list[str]:
    """Flag sentences in key findings sections that assert facts without any citation."""
    uncredited = []
    # Split findings into candidate sentences or bullet points
    lines = findings_text.splitlines()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or len(s) < 25:
            continue
        # If it looks like a finding bullet point or assertion
        if s.startswith("-") or s.startswith("*") or re.match(r"^\d+\.", s):
            if not re.search(r"\[\d+\]", s):
                uncredited.append(s)
    return uncredited


# ---------------------------------------------------------------------------
# Post-Check: Mermaid Validation (Plain Python)
# ---------------------------------------------------------------------------

_VALID_MERMAID_TYPES = {
    "flowchart", "graph", "sequencediagram", "timeline", "mindmap",
    "classdiagram", "statediagram", "statediagram-v2", "pie",
    "gitgraph", "erdiagram", "journey", "c4context",
}


def validate_mermaid_code(code: str) -> tuple[bool, str]:
    """
    Validate Mermaid syntax rules:
    - Starts with a known diagram type keyword
    - Balanced brackets (), [], {}
    - Balanced quotes
    - Contains no raw HTML tags (e.g. <script>, <div, etc.)
    """
    cleaned = code.strip()
    if not cleaned:
        return False, "Mermaid code block is empty"

    # Check for raw HTML tags
    if re.search(r"<\s*script|<\s*iframe|<\s*embed|<\s*style|<\s*link|<\s*div|<\s*a\s+href", cleaned, re.I):
        return False, "Contains prohibited HTML tags"

    lines = [l.strip() for l in cleaned.splitlines() if l.strip() and not l.strip().startswith("%%")]
    if not lines:
        return False, "No active diagram directives"

    first_word = lines[0].split()[0].lower()
    if first_word not in _VALID_MERMAID_TYPES:
        return False, f"Unknown diagram type '{first_word}'. Expected one of {sorted(_VALID_MERMAID_TYPES)}"

    # Check balanced brackets
    counts = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    in_double_quote = False
    in_single_quote = False

    for ch in cleaned:
        if ch == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif ch == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif not in_double_quote and not in_single_quote:
            if ch in counts:
                counts[ch] += 1
            elif ch in pairs:
                open_ch = pairs[ch]
                if counts[open_ch] > 0:
                    counts[open_ch] -= 1
                else:
                    return False, f"Unbalanced closing bracket '{ch}'"

    if in_double_quote:
        return False, 'Unterminated double quote'
    if in_single_quote:
        return False, 'Unterminated single quote'
    for k, v in counts.items():
        if v > 0:
            return False, f"Unbalanced open bracket '{k}'"

    return True, "Valid"


def repair_mermaid_block(
    client: OllamaClient,
    model: str,
    broken_code: str,
    error: str,
    num_ctx: int = 8192,
    stop_flag: Callable[[], bool] | None = None,
) -> str:
    """Attempt one model call to fix the invalid Mermaid diagram."""
    messages = [
        {"role": "system", "content": RESEARCH_MERMAID_REPAIR_SYSTEM},
        {
            "role": "user",
            "content": RESEARCH_MERMAID_REPAIR_USER.format(error=error, diagram=broken_code),
        },
    ]
    opts = {
        "num_ctx": num_ctx,
        "temperature": 0.1,
        "num_predict": sq_num_predict(num_ctx, "repair"),
    }
    chunks: list[str] = []
    try:
        for chunk in client.chat_stream(model=model, messages=messages, options=opts, stop_flag=stop_flag):
            if stop_flag and stop_flag():
                break
            content = chunk.get("message", {}).get("content", "")
            if content:
                chunks.append(content)
            if chunk.get("done"):
                break
    except Exception:
        return ""

    out = "".join(chunks).strip()
    m = re.search(r"```mermaid\s*(.*?)\s*```", out, re.DOTALL | re.I)
    if m:
        return m.group(1).strip()
    return out


def mermaid_to_bullet_fallback(diagram_code: str) -> str:
    """Extract node labels into a readable bullet list when diagram cannot be rendered."""
    nodes = []
    # Find text inside brackets or quotes
    for match in re.finditer(r"\[(.*?)\]|\((.*?)\)|\"(.*?)\"", diagram_code):
        text = next(g for g in match.groups() if g)
        text = re.sub(r"<[^>]+>", "", text).strip()
        if text and len(text) > 2 and text not in nodes:
            nodes.append(text)

    if not nodes:
        return "> *Visual process flow: Details described in the text above.*"

    items = "\n".join(f"- {node}" for node in nodes[:8])
    return f"> ℹ️ *Visual diagram could not be rendered cleanly. Logic outline:*\n{items}"


def process_mermaid_blocks_in_markdown(
    markdown: str,
    client: OllamaClient,
    model: str,
    num_ctx: int = 8192,
    stop_flag: Callable[[], bool] | None = None,
) -> str:
    """Scan Markdown for ```mermaid blocks, validate, attempt repair if needed, or fallback."""
    pattern = re.compile(r"```mermaid\s*(.*?)\s*```", re.DOTALL | re.I)

    def _replace_block(match: re.Match) -> str:
        code = match.group(1).strip()
        is_valid, err = validate_mermaid_code(code)
        if is_valid:
            return f"```mermaid\n{code}\n```"

        # Attempt 1 repair call
        repaired = repair_mermaid_block(client, model, code, err, num_ctx, stop_flag)
        if repaired:
            rep_valid, _ = validate_mermaid_code(repaired)
            if rep_valid:
                return f"```mermaid\n{repaired}\n```"

        # Fallback to bullet list
        fallback = mermaid_to_bullet_fallback(code)
        return fallback

    return pattern.sub(_replace_block, markdown)


# ---------------------------------------------------------------------------
# Section Generation Helpers
# ---------------------------------------------------------------------------

def _format_ledger_for_prompt(ledger: list[dict], max_items: int = 25) -> str:
    lines = []
    for entry in ledger[:max_items]:
        sids = ", ".join(f"[{sid}]" for sid in entry.get("source_ids", []))
        lines.append(
            f"- Claim: {entry.get('claim')} "
            f"(Status: {entry.get('status')}, Confidence: {entry.get('confidence')}) "
            f"Sources: {sids}"
        )
    return "\n".join(lines)


def _format_sources_for_prompt(sources: list[dict]) -> str:
    lines = []
    for s in sources:
        lines.append(f"[{s.get('id')}] {s.get('title')} ({s.get('domain')}, Credibility: {s.get('credibility')})")
    return "\n".join(lines)


def generate_single_section(
    client: OllamaClient,
    model: str,
    topic: str,
    section_title: str,
    instructions: str,
    context: str,
    num_ctx: int = 8192,
    stop_flag: Callable[[], bool] | None = None,
) -> str:
    """Generate one Markdown section under the token budget."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    sys_prompt = RESEARCH_SECTION_SYSTEM.format(date=today_str)

    budget = input_budget(num_ctx, fraction=0.45)
    trimmed_context = trim_text_to_budget(context, budget_tokens=budget - 250)

    user_prompt = RESEARCH_SECTION_USER.format(
        topic=topic,
        section_title=section_title,
        instructions=instructions,
        context=trimmed_context,
    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]

    opts = {
        "num_ctx": num_ctx,
        "temperature": 0.3,
        "num_predict": sq_num_predict(num_ctx, "section"),
    }

    chunks: list[str] = []
    for chunk in client.chat_stream(model=model, messages=messages, options=opts, stop_flag=stop_flag):
        if stop_flag and stop_flag():
            break
        content = chunk.get("message", {}).get("content", "")
        if content:
            chunks.append(content)
        if chunk.get("done"):
            break

    return "".join(chunks).strip()


# ---------------------------------------------------------------------------
# Deterministic Section Builders (Sections 10 & 11)
# ---------------------------------------------------------------------------

def build_section_10_methodology(
    sources: list[dict],
    ledger: list[dict],
    dropped_quotes: int,
) -> str:
    """Section 10: How this research was done (exact deterministic stats)."""
    today_str = datetime.now().strftime("%B %d, %Y")
    total_sources = len(sources)
    corroborated = sum(1 for c in ledger if c.get("status") == "corroborated")
    single_source = sum(1 for c in ledger if c.get("status") == "single-source")
    contested = sum(1 for c in ledger if c.get("status") == "contested")

    high_cred = sum(1 for s in sources if s.get("credibility", 0) >= 0.70)
    avg_cred = (sum(s.get("credibility", 0) for s in sources) / max(total_sources, 1))

    return f"""## 10. How this research was done

- **Date of Research**: {today_str}
- **Sources Evaluated**: {total_sources} web sources analyzed across independent registered domains.
- **Quote Verification**: Every claim extracted was verified verbatim against original page text. **{dropped_quotes}** extracted statements were dropped because matching verbatim quotes could not be confirmed.
- **Claim Corroboration**:
  - **{corroborated}** claims corroborated across multiple independent domains.
  - **{single_source}** claims verified by a single authoritative domain.
  - **{contested}** claims identified as contested or conflicting across sources.
- **Credibility Profile**: {high_cred} of {total_sources} sources met high-credibility criteria (official institutions, documentation standards, or academic indices). Overall source credibility average: **{avg_cred:.2f} / 1.00**.
"""


def build_section_11_sources_table(sources: list[dict]) -> str:
    """Section 11: Sources table (deterministic Markdown table)."""
    rows = [
        "## 11. Sources",
        "",
        "| # | Source Title | Domain | Publish Date | Credibility | Link |",
        "|---|--------------|--------|--------------|-------------|------|",
    ]
    for s in sources:
        sid = s.get("id", 1)
        title = s.get("title", "").replace("|", "-") or s.get("domain", "Source")
        domain = s.get("domain", "")
        date_val = s.get("date", "Undated")
        cred = s.get("credibility", 0.5)
        reason = s.get("credibility_reason", "").replace("|", "-")
        url = s.get("url", "")
        rows.append(
            f"| [{sid}] | {title} | `{domain}` | {date_val} | **{cred:.2f}** ({reason}) | [{domain}]({url}) |"
        )
    return "\n".join(rows) + "\n"


# ---------------------------------------------------------------------------
# Complete Report Writer Orchestration
# ---------------------------------------------------------------------------

class ResearchReportWriter:
    """
    Writes the report one section at a time.
    Checkpointing each section, validating citations and Mermaid blocks.
    """

    def __init__(
        self,
        project_id: str,
        topic: str,
        plan_dict: dict,
        sources: list[dict],
        ledger: list[dict],
        client: OllamaClient,
        model: str,
        num_ctx: int = 8192,
        dropped_quotes: int = 0,
        progress_cb: Callable[[str, int, int, str], None] | None = None,
        section_cb: Callable[[str, str], None] | None = None,
        stop_flag: Callable[[], bool] | None = None,
    ) -> None:
        self.project_id = project_id
        self.topic = topic
        self.plan_dict = plan_dict
        self.sources = sources
        self.ledger = ledger
        self.client = client
        self.model = model
        self.num_ctx = num_ctx
        self.dropped_quotes = dropped_quotes
        self.progress_cb = progress_cb
        self.section_cb = section_cb
        self.stop_flag = stop_flag
        self.sections: list[str] = []

    def _notify(self, step: int, total: int, msg: str) -> None:
        if self.progress_cb:
            self.progress_cb("Writing", step, total, msg)

    def write_report(self) -> str:
        """Generate all sections and assemble the final report."""
        valid_sids = {s.get("id", 0) for s in self.sources if s.get("id")}
        ledger_ctx = _format_ledger_for_prompt(self.ledger)
        sources_ctx = _format_sources_for_prompt(self.sources)
        full_context = f"### Verified Ledger:\n{ledger_ctx}\n\n### Available Sources:\n{sources_ctx}"

        # Section definitions (Sections 1-9 generated via model, 10-11 deterministic)
        section_plans = [
            (
                "Title & Executive Summary",
                "Write the report Title (# [Title]) and '## 1. TL;DR in plain English'. "
                "Provide 3 to 5 short, impactful sentences summarizing the core findings. Plain English.",
            ),
            (
                "Key Findings",
                "Write '## 2. Key Findings'. Provide numbered bullet points. "
                "Every finding MUST include citation numbers like [1] or [1][2], and a confidence label "
                "(High, Medium, or Low) with a concise one-line reason.",
            ),
            (
                "What is Disputed or Uncertain",
                "Write '## 3. What is Disputed or Uncertain'. Show contested or conflicting claims side by side, "
                "clearly identifying who says what with source citations.",
            ),
            (
                "Myths and Misconceptions Checked",
                "Write '## 4. Myths and Misconceptions Checked'. Review the skeptic checklist topics and state "
                "what the empirical evidence actually showed.",
            ),
            (
                "How It Works",
                "Write '## 5. How It Works'. Explain the underlying process or mechanisms. "
                "Include a clean Mermaid flowchart diagram (` ```mermaid `) showing the logic flow or relationships. "
                "Avoid HTML tags inside the diagram; use clear node labels and standard syntax.",
            ),
            (
                "Glossary",
                "Write '## 6. Glossary'. List every technical or uncommon term used in this report, "
                "explaining each in one simple sentence.",
            ),
            (
                "Explain It Like I'm 12",
                "Write '## 7. Explain It Like I\\'m 12'. Provide a short, plain explanation "
                "using an accessible real-life analogy.",
            ),
            (
                "Test Yourself",
                "Write '## 8. Test Yourself'. Provide 5 short quiz questions to test understanding, "
                "with the answers hidden inside a Markdown details block: <details><summary>Click to view answers</summary>...</details>.",
            ),
            (
                "Open Questions & Next Steps",
                "Write '## 9. What We Still Don\\'t Know'. Identify missing information, "
                "unresolved questions, and suggested next topics to research.",
            ),
        ]

        total_steps = len(section_plans) + 2

        for idx, (title, instr) in enumerate(section_plans, start=1):
            if self.stop_flag and self.stop_flag():
                break

            self._notify(idx, total_steps, f"Writing section {idx}: {title}...")
            sec_text = generate_single_section(
                client=self.client,
                model=self.model,
                topic=self.topic,
                section_title=title,
                instructions=instr,
                context=full_context,
                num_ctx=self.num_ctx,
                stop_flag=self.stop_flag,
            )

            # Validate and repair Mermaid diagrams in Section 5
            if "mermaid" in sec_text.lower():
                sec_text = process_mermaid_blocks_in_markdown(
                    sec_text,
                    client=self.client,
                    model=self.model,
                    num_ctx=self.num_ctx,
                    stop_flag=self.stop_flag,
                )

            # Validate citations
            sec_text, _ = validate_citations(sec_text, valid_sids)

            self.sections.append(sec_text)
            if self.section_cb:
                self.section_cb(title, sec_text)

            # Checkpoint partial report
            partial_md = "\n\n".join(self.sections)
            save_report(self.project_id, partial_md)
            save_status(self.project_id, {"phase": "writing", "section_index": idx})

        # Add Section 10 (Deterministic methodology)
        if not (self.stop_flag and self.stop_flag()):
            self._notify(10, total_steps, "Compiling methodology and audit statistics...")
            sec_10 = build_section_10_methodology(self.sources, self.ledger, self.dropped_quotes)
            self.sections.append(sec_10)

        # Add Section 11 (Deterministic sources table)
        if not (self.stop_flag and self.stop_flag()):
            self._notify(11, total_steps, "Assembling sources reference table...")
            sec_11 = build_section_11_sources_table(self.sources)
            self.sections.append(sec_11)

        final_md = "\n\n".join(self.sections)
        save_report(self.project_id, final_md)
        save_status(self.project_id, {"phase": "completed", "section_index": 11})
        update_index_entry(self.project_id, status="completed")
        return final_md
