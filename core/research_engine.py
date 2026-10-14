"""
core/research_engine.py - Core research execution engine.

Handles:
- Web search & page fetching per sub-question
- Quote verification (pure Python)
- Source credibility scoring (pure Python)
- Independence checking & near-duplicate detection (pure Python)
- Claim ledger grouping and status/confidence calculation
- Mandatory counter-search for skepticism
- Adversarial skeptic pass (single bounded model call)
- Checkpoint persistence after every sub-question
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlparse

from core.ollama_client import OllamaClient
from core.prompts import (
    RESEARCH_EVIDENCE_SYSTEM,
    RESEARCH_EVIDENCE_USER,
    RESEARCH_SKEPTIC_SYSTEM,
    RESEARCH_SKEPTIC_USER,
    RESEARCH_LEDGER_SYSTEM,
)
from core.research_budget import (
    input_budget,
    sq_num_predict,
    trim_chunks_to_budget,
    trim_text_to_budget,
)
from core.research_planner import SubQuestion, ResearchPlan
from core.research_store import (
    save_notes,
    save_ledger,
    save_sources,
    save_status,
    update_index_entry,
)
from core.web_research import (
    search,
    fetch_pages,
    extract_text,
    chunk_and_rank,
    extract_urls,
    normalize_url,
)


# ---------------------------------------------------------------------------
# 1. Quote Verification (Plain Python)
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Normalize whitespace and lower-case text for comparisons."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _get_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w]


def verify_quote(quote: str, page_text: str, tolerance: float = 0.8) -> bool:
    """
    Verify that an extracted quote actually appears in the fetched page text.
    First checks for exact substring match (case and whitespace normalized).
    If that fails, checks for fuzzy word-shingle / token overlap >= tolerance.
    """
    if not quote or not page_text:
        return False

    norm_quote = normalize_text(quote)
    norm_page = normalize_text(page_text)

    # 1. Direct normalized match
    if norm_quote in norm_page:
        return True

    # 2. Fuzzy match: word sequence sliding window
    quote_words = _get_words(norm_quote)
    if not quote_words:
        return False

    q_len = len(quote_words)
    page_words = _get_words(norm_page)
    if len(page_words) < q_len:
        # Check overall Jaccard of words if page is short
        q_set = set(quote_words)
        p_set = set(page_words)
        intersect = len(q_set & p_set)
        return (intersect / len(q_set)) >= tolerance

    quote_set = set(quote_words)
    # Check sliding windows of length q_len +- 2
    step = max(1, q_len // 3)
    best_ratio = 0.0
    for i in range(0, len(page_words) - q_len + 1, step):
        window = set(page_words[i : i + q_len])
        overlap = len(quote_set & window) / len(quote_set)
        if overlap > best_ratio:
            best_ratio = overlap
        if best_ratio >= tolerance:
            return True

    return best_ratio >= tolerance


# ---------------------------------------------------------------------------
# 2. Credibility Score (Plain Python, No Model Call)
# ---------------------------------------------------------------------------

_HIGH_CRED_DOMAINS = {
    "gov", "mil", "edu",
}

_TRUSTED_AUTHORITY_SITES = {
    "w3.org", "ietf.org", "iso.org", "ieee.org", "nature.com",
    "sciencedirect.com", "ncbi.nlm.nih.gov", "nih.gov", "cdc.gov",
    "who.int", "arxiv.org", "wikipedia.org", "britannica.com",
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "npr.org",
    "github.com", "python.org", "gnu.org", "kernel.org",
}

_LOW_CRED_PATTERNS = [
    r"blog", r"wordpress", r"blogspot", r"medium\.com", r"substack\.com",
    r"reddit\.com", r"quora\.com", r"pinterest\.com", r"buzzfeed\.com",
    r"tumblr\.com", r"facebook\.com", r"twitter\.com", r"x\.com",
]


def extract_registered_domain(url: str) -> str:
    """Extract registered domain name from URL (e.g. news.bbc.co.uk -> bbc.co.uk)."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        parts = host.split(".")
        if len(parts) <= 2:
            return host
        # Handle 2-part ccTLDs like .co.uk, .gov.uk, .com.au
        if len(parts) >= 3 and parts[-2] in ("co", "gov", "edu", "org", "ac", "com") and len(parts[-1]) == 2:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])
    except Exception:
        return url


def detect_author_and_date(text: str) -> tuple[bool, str]:
    """Detect if an author or date is mentioned in text."""
    has_author = False
    date_str = ""

    # Author pattern
    if re.search(r"\b(?:by|author|written by):\s*[A-Z][a-z]+ [A-Z][a-z]+", text, re.I):
        has_author = True

    # Date pattern (e.g. 2024-05-12 or May 12, 2024)
    m_date = re.search(
        r"\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|"
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? (?:19|20)\d{2}\b",
        text,
        re.I,
    )
    if m_date:
        date_str = m_date.group(0)

    return has_author, date_str


def credibility_score(
    url: str,
    text: str = "",
    has_author: bool | None = None,
    date_str: str | None = None,
) -> tuple[float, str]:
    """
    Calculate credibility score between 0.0 and 1.0 based on purely deterministic rules.
    Returns (score, reason).
    """
    if not url:
        return (0.2, "No URL provided")

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    reg_domain = extract_registered_domain(url)

    reasons: list[str] = []
    base_score = 0.50

    # 1. Protocol
    if scheme == "https":
        base_score += 0.05
    else:
        base_score -= 0.15
        reasons.append("Non-HTTPS")

    # 2. TLD and domain authority
    tld = host.split(".")[-1]
    if tld in _HIGH_CRED_DOMAINS:
        base_score += 0.35
        reasons.append(f"Official .{tld} domain")
    elif reg_domain in _TRUSTED_AUTHORITY_SITES or host in _TRUSTED_AUTHORITY_SITES:
        base_score += 0.30
        reasons.append("Recognized authority / standards institution")
    else:
        # Check low cred patterns
        is_low = False
        for pat in _LOW_CRED_PATTERNS:
            if re.search(pat, host, re.I):
                base_score -= 0.25
                reasons.append("User-generated content or blog platform")
                is_low = True
                break
        if not is_low:
            reasons.append("Standard commercial or organizational domain")

    # 3. Author and Date checks
    if text and (has_author is None or date_str is None):
        det_author, det_date = detect_author_and_date(text)
        if has_author is None:
            has_author = det_author
        if date_str is None:
            date_str = det_date

    if has_author:
        base_score += 0.05
        reasons.append("Named author identified")

    if date_str:
        base_score += 0.05
        reasons.append(f"Publication date identified ({date_str})")

    score = max(0.05, min(1.0, round(base_score, 2)))
    return (score, "; ".join(reasons))


# ---------------------------------------------------------------------------
# 3. Independence Check & Near-Duplicate Detection (Plain Python)
# ---------------------------------------------------------------------------

def is_same_domain(url1: str, url2: str) -> bool:
    """Return True if both URLs belong to the same registered domain."""
    return extract_registered_domain(url1) == extract_registered_domain(url2)


def get_word_shingles(text: str, k: int = 4) -> set[tuple[str, ...]]:
    """Return k-word shingles from text."""
    words = _get_words(text)
    if len(words) < k:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + k]) for i in range(len(words) - k + 1)}


def jaccard_shingle_similarity(text1: str, text2: str, k: int = 4) -> float:
    """Compute Jaccard similarity between two texts using k-word shingles."""
    s1 = get_word_shingles(text1, k)
    s2 = get_word_shingles(text2, k)
    if not s1 or not s2:
        return 0.0
    intersection = len(s1 & s2)
    union = len(s1 | s2)
    return intersection / union if union > 0 else 0.0


def are_near_duplicates(text1: str, text2: str, threshold: float = 0.50) -> bool:
    """Determine if two texts are syndicated copies or near-duplicates."""
    return jaccard_shingle_similarity(text1, text2) >= threshold


# ---------------------------------------------------------------------------
# 4. Claim Ledger & Confidence Calculation
# ---------------------------------------------------------------------------

@dataclass
class ClaimEntry:
    id: str
    claim: str
    sub_question_index: int
    quotes: list[dict] = field(default_factory=list)  # {quote, source_id, url}
    source_ids: list[int] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    status: str = "single-source"  # corroborated, contested, single-source, unverified
    confidence: str = "Low"         # High, Medium, Low
    confidence_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def calculate_claim_confidence(
    claim_status: str,
    source_ids: list[int],
    sources_map: dict[int, dict],
    independent_domains_count: int,
) -> tuple[str, str]:
    """
    Compute confidence (High, Medium, Low) and explanatory reason in plain Python.
    """
    if claim_status == "unverified" or not source_ids:
        return "Low", "No verified quote in source text"

    # Compute average credibility of sources
    cred_scores = [sources_map[sid].get("credibility", 0.5) for sid in source_ids if sid in sources_map]
    avg_cred = sum(cred_scores) / len(cred_scores) if cred_scores else 0.5

    if claim_status == "contested":
        return "Low", "Sources contradict or dispute this claim"

    if claim_status == "corroborated" and independent_domains_count >= 2:
        if avg_cred >= 0.70:
            return "High", f"Corroborated across {independent_domains_count} independent high-credibility sources"
        return "Medium", f"Corroborated across {independent_domains_count} independent sources with moderate credibility"

    # Single-source
    if avg_cred >= 0.85:
        return "Medium", "Single authoritative source (high credibility standards/gov institution)"
    return "Low", "Single source, uncorroborated by independent domains"


def claims_similarity(c1: str, c2: str) -> float:
    """Word-overlap similarity between two claim sentences."""
    w1 = set(_get_words(c1))
    w2 = set(_get_words(c2))
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / max(len(w1), len(w2))


def group_and_update_ledger(
    ledger: list[dict],
    new_claims: list[dict],
    sources_map: dict[int, dict],
    sq_index: int,
) -> list[dict]:
    """
    Group new extracted claims into the claim ledger.
    Matches similar claims using word overlap first.
    Updates claim status, independent domains, and confidence.
    """
    updated = [dict(c) for c in ledger]

    for nc in new_claims:
        claim_text = nc.get("claim", "").strip()
        quote = nc.get("exact_quote", "").strip()
        source_id = nc.get("source_id", 0)
        url = nc.get("url", "")
        domain = extract_registered_domain(url)

        # Look for matching claim in ledger
        matched_idx = -1
        best_sim = 0.0
        for i, entry in enumerate(updated):
            if entry.get("sub_question_index") != sq_index:
                continue
            sim = claims_similarity(claim_text, entry.get("claim", ""))
            if sim > 0.55 and sim > best_sim:
                best_sim = sim
                matched_idx = i

        if matched_idx >= 0:
            # Merge into existing claim
            entry = updated[matched_idx]
            entry_sids = set(entry.get("source_ids", []))
            entry_sids.add(source_id)
            entry["source_ids"] = list(entry_sids)

            entry_domains = set(entry.get("domains", []))
            entry_domains.add(domain)
            entry["domains"] = list(entry_domains)

            quotes_list = entry.setdefault("quotes", [])
            quotes_list.append({"quote": quote, "source_id": source_id, "url": url})

            # Check status
            if len(entry["domains"]) >= 2:
                entry["status"] = "corroborated"
            else:
                entry["status"] = "single-source"

            conf, reason = calculate_claim_confidence(
                entry["status"],
                entry["source_ids"],
                sources_map,
                len(entry["domains"]),
            )
            entry["confidence"] = conf
            entry["confidence_reason"] = reason
        else:
            # New claim entry
            entry_id = f"claim_{len(updated) + 1}"
            status = "single-source"
            conf, reason = calculate_claim_confidence(
                status,
                [source_id],
                sources_map,
                1,
            )
            updated.append({
                "id": entry_id,
                "claim": claim_text,
                "sub_question_index": sq_index,
                "quotes": [{"quote": quote, "source_id": source_id, "url": url}],
                "source_ids": [source_id],
                "domains": [domain],
                "status": status,
                "confidence": conf,
                "confidence_reason": reason,
            })

    return updated


# ---------------------------------------------------------------------------
# 5. Model Execution: Evidence Notes Extraction
# ---------------------------------------------------------------------------

def extract_evidence_from_source(
    client: OllamaClient,
    model: str,
    question: str,
    source: dict,
    num_ctx: int = 8192,
    stop_flag: Callable[[], bool] | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Feed source text chunks under the token budget to the model.
    Returns list of parsed claims (at most 3): [{claim, exact_quote, context}].
    """
    url = source.get("url", "")
    text = source.get("text", "")
    if not text:
        return []

    # Token budget trimming
    budget = input_budget(num_ctx, fraction=0.55)
    trimmed_text = trim_text_to_budget(text, budget_tokens=budget - 200)

    user_msg = (
        f"## Research Sub-Question\n{question}\n\n"
        f"## Source URL\n{url}\n\n"
        f"## Page Text (Untrusted Data)\n```\n{trimmed_text}\n```\n\n"
        "Extract at most 3 relevant claims with exact quotes. Return ONLY JSON matching {\"claims\": [...]}. Limit to at most 3 claims."
    )

    messages = [
        {"role": "system", "content": RESEARCH_EVIDENCE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]

    opts = {
        "num_ctx": num_ctx,
        "temperature": 0.2,
        "num_predict": sq_num_predict(num_ctx, "evidence"),
    }

    chunks: list[str] = []
    try:
        for chunk in client.chat_stream(
            model=model,
            messages=messages,
            options=opts,
            format_schema="json",
            stop_flag=stop_flag,
        ):
            if stop_flag and stop_flag():
                return []
            content = chunk.get("message", {}).get("content", "")
            if content:
                chunks.append(content)
            if chunk.get("done"):
                break
    except Exception as exc:
        if progress_cb:
            progress_cb(f"⚠️ Model call failed for {source.get('domain', url)}: {exc}")
        return []

    full_output = "".join(chunks).strip()
    try:
        data = json.loads(full_output)
        claims = data.get("claims", [])
        if isinstance(claims, list):
            return claims[:3]
    except Exception:
        # Fallback regex search for json block
        m = re.search(r"\{.*\}", full_output, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                claims = data.get("claims", [])
                if isinstance(claims, list):
                    return claims[:3]
            except Exception:
                pass
        # Surface truncated or invalid JSON
        import logging
        logging.warning("Invalid/truncated JSON from model for %s: %s", source.get("domain", url), full_output[:120])
        if progress_cb:
            domain = source.get("domain") or urlparse(url).netloc
            progress_cb(f"⚠️ Model returned invalid or truncated JSON when analyzing {domain}")
    return []


# ---------------------------------------------------------------------------
# 6. Model Execution: Skeptic Pass
# ---------------------------------------------------------------------------

def run_skeptic_pass(
    client: OllamaClient,
    model: str,
    question: str,
    ledger_entries: list[dict],
    num_ctx: int = 8192,
    stop_flag: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    Run adversarial skeptic pass on the sub-question ledger.
    Receives only ledger data, not raw web pages.
    """
    if not ledger_entries:
        return {}

    today_str = datetime.now().strftime("%Y-%m-%d")
    system_prompt = RESEARCH_SKEPTIC_SYSTEM.format(date=today_str)

    ledger_summary = []
    for item in ledger_entries:
        ledger_summary.append(
            f"- [{item.get('status')}, Confidence: {item.get('confidence')}] "
            f"Claim: {item.get('claim')} (Sources: {len(item.get('source_ids', []))})"
        )
    ledger_text = "\n".join(ledger_summary)

    user_msg = (
        f"## Sub-Question\n{question}\n\n"
        f"## Claim Ledger\n{ledger_text}\n\n"
        "Perform the skeptic pass and return ONLY valid JSON."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    opts = {
        "num_ctx": num_ctx,
        "temperature": 0.3,
        "num_predict": sq_num_predict(num_ctx, "skeptic"),
    }

    chunks: list[str] = []
    try:
        for chunk in client.chat_stream(
            model=model,
            messages=messages,
            options=opts,
            format_schema="json",
            stop_flag=stop_flag,
        ):
            if stop_flag and stop_flag():
                return {}
            content = chunk.get("message", {}).get("content", "")
            if content:
                chunks.append(content)
            if chunk.get("done"):
                break
    except Exception:
        return {}

    full_output = "".join(chunks).strip()
    try:
        return json.loads(full_output)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 7. Research Engine Orchestration
# ---------------------------------------------------------------------------

class ResearchEngine:
    """
    Executes deep research workflow across all sub-questions.
    Preserves token budgets, enforces single model call at a time,
    verifies quotes, scores credibility, and checkpoints progress.
    """

    def __init__(
        self,
        project_id: str,
        plan: ResearchPlan,
        ollama_url: str,
        model: str,
        num_ctx: int = 8192,
        depth: str = "standard",
        progress_cb: Callable[[str, int, int, str], None] | None = None,
        stop_flag: Callable[[], bool] | None = None,
        client: OllamaClient | None = None,
        topic: str = "",
    ) -> None:
        self.project_id = project_id
        self.plan = plan
        self.ollama_url = ollama_url
        self.model = model
        self.num_ctx = num_ctx
        self.depth = depth.lower()
        self.progress_cb = progress_cb
        self.stop_flag = stop_flag
        self._own_client = False
        self.client = client
        if self.client is None:
            self.client = OllamaClient(ollama_url)
            self._own_client = True
        self.topic = topic or (getattr(self.plan, "topic", "") if self.plan else "") or (getattr(self.plan, "main_question", "") if self.plan else "")

        # Depth limits
        if self.depth == "quick":
            self.max_follow_ups = 0
            self.max_pages_per_sq = 4
            self.max_total_pages = 10
        elif self.depth == "thorough":
            self.max_follow_ups = 2
            self.max_pages_per_sq = 8
            self.max_total_pages = 25
        else:  # standard
            self.max_follow_ups = 1
            self.max_pages_per_sq = 6
            self.max_total_pages = 16

        # State
        self.sources: list[dict] = []
        self.sources_map: dict[int, dict] = {}
        self.notes: list[dict] = []
        self.ledger: list[dict] = []
        self.dropped_quotes_count: int = 0
        self.total_pages_fetched: int = 0

    def _notify(self, phase: str, step: int, total: int, msg: str) -> None:
        if self.progress_cb:
            self.progress_cb(phase, step, total, msg)

    def _is_stopped(self) -> bool:
        return self.stop_flag is not None and self.stop_flag()

    def run_subquestion(self, sq: SubQuestion, sq_idx: int, total_sqs: int) -> None:
        """Execute research pipeline for a single sub-question."""
        if self._is_stopped():
            return

        self._notify("Searching", sq_idx, total_sqs, f"🌐 Web Search: Starting SQ {sq.index}: {sq.question[:45]}…")
        new_source_notes: list[dict] = []

        # Direct URL support: if topic contains a URL, fetch directly first on SQ 1
        topic_urls = extract_urls(self.topic) if self.topic else []
        is_single_site = bool(
            topic_urls
            and (
                len(topic_urls) == 1
                and (
                    len(self.topic.split()) <= 4
                    or topic_urls[0] in self.topic
                    or self.topic.strip().startswith("http")
                )
            )
        )

        if sq_idx == 1 and topic_urls:
            for seed_url in topic_urls:
                if self._is_stopped():
                    return
                if not any(s.get("url") == seed_url for s in self.sources):
                    self._notify("Searching", sq_idx, total_sqs, f"🌐 Direct Seed URL: Fetching {seed_url}…")
                    seed_fetched = fetch_pages([seed_url])
                    self.total_pages_fetched += len(seed_fetched)
                    for s_page in seed_fetched:
                        if s_page.get("error"):
                            self._notify(
                                "Searching",
                                sq_idx,
                                total_sqs,
                                f"⚠️ Could not fetch seeded URL {seed_url}: {s_page.get('error')}",
                            )
                            continue
                        s_html = s_page.get("html", "")
                        s_text = extract_text(s_html, url=seed_url)
                        if not s_text or len(s_text.strip()) < 100:
                            continue
                        s_cred, s_reason = credibility_score(seed_url, s_text)
                        s_id = len(self.sources) + 1
                        s_domain = extract_registered_domain(seed_url) or urlparse(seed_url).netloc
                        s_entry = {
                            "id": s_id,
                            "url": seed_url,
                            "domain": s_domain,
                            "text": s_text,
                            "credibility": s_cred,
                            "credibility_reason": s_reason,
                            "title": urlparse(seed_url).netloc,
                        }
                        self.sources.append(s_entry)
                        self.sources_map[s_id] = s_entry

                        self._notify("Checking facts", sq_idx, total_sqs, f"🔍 Extracting claims from {s_domain}")
                        s_claims = extract_evidence_from_source(
                            client=self.client,
                            model=self.model,
                            question=sq.question,
                            source=s_entry,
                            num_ctx=self.num_ctx,
                            stop_flag=self.stop_flag,
                            progress_cb=lambda msg: self._notify("Checking facts", sq_idx, total_sqs, msg),
                        )
                        s_verified = []
                        s_dropped = 0
                        for c in s_claims:
                            if verify_quote(c.get("exact_quote", ""), s_text):
                                c["source_id"] = s_id
                                c["url"] = seed_url
                                s_verified.append(c)
                            else:
                                self.dropped_quotes_count += 1
                                s_dropped += 1
                        self._notify(
                            "Checking facts",
                            sq_idx,
                            total_sqs,
                            f"🛡️ Kept {len(s_verified)} claims, dropped {s_dropped} unverifiable quotes from {s_domain}",
                        )
                        if s_verified:
                            new_source_notes.append({
                                "source_id": s_id,
                                "url": seed_url,
                                "sub_question_index": sq.index,
                                "claims": s_verified,
                            })
                            self.ledger = group_and_update_ledger(
                                self.ledger,
                                s_verified,
                                self.sources_map,
                                sq.index,
                            )

        # a) Gather queries: initial plan queries + mandatory counter query (skip if single-site)
        queries = list(sq.search_queries[:2])
        if not is_single_site:
            counter_query = f"criticism of {sq.question[:40]} problems debunked"
            queries.append(counter_query)

        # Collect URLs from search with 1s pause between queries to avoid DDG rate limits
        urls_to_fetch: list[str] = []
        for i, q in enumerate(queries):
            if self._is_stopped():
                return
            if i > 0:
                time.sleep(1.0)
            self._notify("Searching", sq_idx, total_sqs, f"🌐 Web Search: DuckDuckGo query '{q[:40]}…'")
            results = search(q, max_results=4)
            if results and (results[0].get("url") == "" or "error" in results[0]):
                err_msg = results[0].get("error") or results[0].get("snippet") or "Search failed"
                self._notify("Searching", sq_idx, total_sqs, f"⚠️ Search failed: {err_msg}")
            else:
                self._notify(
                    "Searching",
                    sq_idx,
                    total_sqs,
                    f"🌐 Web Search: Query '{q[:35]}…' returned {len(results)} results",
                )
                for r in results:
                    url = r.get("url", "")
                    if url and url not in urls_to_fetch:
                        urls_to_fetch.append(url)

        # Limit pages per SQ
        urls_to_fetch = urls_to_fetch[: self.max_pages_per_sq]

        # Fetch and extract pages (parallel network fetch)
        domains_preview = ", ".join(extract_registered_domain(u) for u in urls_to_fetch[:3])
        self._notify("Reading", sq_idx, total_sqs, f"🌐 Web Fetch: Downloading {len(urls_to_fetch)} pages ({domains_preview}…)")
        fetched = fetch_pages(urls_to_fetch)
        self.total_pages_fetched += len(fetched)

        # Surface fetch errors
        for p in fetched:
            if p.get("error"):
                domain_err = extract_registered_domain(p.get("url", "")) or urlparse(p.get("url", "")).netloc
                self._notify("Reading", sq_idx, total_sqs, f"⚠️ Could not fetch {domain_err}: {p.get('error')}")

        # Process each page with granular progress
        valid_pages = [p for p in fetched if p.get("html")]
        total_valid = len(valid_pages)

        for page_idx, page in enumerate(valid_pages, start=1):
            if self._is_stopped():
                return

            url = page.get("url", "")
            raw_html = page.get("html", "")
            domain = extract_registered_domain(url) or urlparse(url).netloc
            self._notify("Reading", sq_idx, total_sqs, f"📄 Reading page {page_idx} of {total_valid}: {domain}")

            text = extract_text(raw_html, url=url)
            if not text or len(text.strip()) < 100:
                continue

            if "[Note: This website renders content dynamically with JavaScript" in text:
                self._notify(
                    "Reading",
                    sq_idx,
                    total_sqs,
                    f"ℹ️ Site renders with JavaScript — limited static info extracted from {domain}",
                )

            # Check near-duplicates against existing sources
            duplicate = False
            for prev in self.sources:
                if are_near_duplicates(text, prev.get("text", "")):
                    duplicate = True
                    break
            if duplicate:
                continue

            # Credibility score (Plain Python)
            cred, reason = credibility_score(url, text)

            source_id = len(self.sources) + 1
            source_entry = {
                "id": source_id,
                "url": url,
                "domain": domain,
                "text": text,
                "credibility": cred,
                "credibility_reason": reason,
                "title": urlparse(url).netloc,
            }
            self.sources.append(source_entry)
            self.sources_map[source_id] = source_entry

            # b) Extract claims with exact quotes (one model call at a time)
            self._notify("Checking facts", sq_idx, total_sqs, f"🔍 Extracting claims from {domain}")
            raw_claims = extract_evidence_from_source(
                client=self.client,
                model=self.model,
                question=sq.question,
                source=source_entry,
                num_ctx=self.num_ctx,
                stop_flag=self.stop_flag,
                progress_cb=lambda msg: self._notify("Checking facts", sq_idx, total_sqs, msg),
            )

            # c) Quote verification in plain Python
            verified_claims: list[dict] = []
            dropped_this_page = 0
            for c in raw_claims:
                quote = c.get("exact_quote", "")
                if verify_quote(quote, text):
                    c["source_id"] = source_id
                    c["url"] = url
                    verified_claims.append(c)
                else:
                    self.dropped_quotes_count += 1
                    dropped_this_page += 1

            self._notify(
                "Checking facts",
                sq_idx,
                total_sqs,
                f"🛡️ Kept {len(verified_claims)} claims, dropped {dropped_this_page} unverifiable quotes from {domain}",
            )

            if verified_claims:
                new_source_notes.append({
                    "source_id": source_id,
                    "url": url,
                    "sub_question_index": sq.index,
                    "claims": verified_claims,
                })
                # f) Group into claim ledger
                self.ledger = group_and_update_ledger(
                    self.ledger,
                    verified_claims,
                    self.sources_map,
                    sq.index,
                )

        self.notes.extend(new_source_notes)

        # h) Adversarial Skeptic Pass
        if self._is_stopped():
            return

        sq_ledger = [c for c in self.ledger if c.get("sub_question_index") == sq.index]
        if sq_ledger and self.max_follow_ups > 0:
            self._notify("Checking facts", sq_idx, total_sqs, f"Running skeptical review for SQ {sq.index}...")
            skeptic_res = run_skeptic_pass(
                client=self.client,
                model=self.model,
                question=sq.question,
                ledger_entries=sq_ledger,
                num_ctx=self.num_ctx,
                stop_flag=self.stop_flag,
            )

            # Run follow-up queries if recommended
            follow_ups = skeptic_res.get("follow_up_queries", [])
            if isinstance(follow_ups, list) and follow_ups:
                for fq in follow_ups[: self.max_follow_ups]:
                    if self._is_stopped():
                        break
                    self._notify("Checking facts", sq_idx, total_sqs, f"🌐 Follow-up Web Search: '{str(fq)[:40]}…'")
                    f_results = search(str(fq), max_results=2)
                    f_urls = [r.get("url") for r in f_results if r.get("url")]
                    if f_urls:
                        self._notify("Checking facts", sq_idx, total_sqs, f"🌐 Web Fetch: Downloading {len(f_urls)} follow-up pages…")
                    f_fetched = fetch_pages(f_urls)
                    for f_page in f_fetched:
                        f_text = extract_text(f_page.get("html", ""))
                        if not f_text:
                            continue
                        f_url = f_page.get("url", "")
                        f_cred, f_reason = credibility_score(f_url, f_text)
                        f_sid = len(self.sources) + 1
                        f_source = {
                            "id": f_sid,
                            "url": f_url,
                            "domain": extract_registered_domain(f_url),
                            "text": f_text,
                            "credibility": f_cred,
                            "credibility_reason": f_reason,
                            "title": urlparse(f_url).netloc,
                        }
                        self.sources.append(f_source)
                        self.sources_map[f_sid] = f_source
                        f_claims = extract_evidence_from_source(
                            client=self.client,
                            model=self.model,
                            question=sq.question,
                            source=f_source,
                            num_ctx=self.num_ctx,
                            stop_flag=self.stop_flag,
                        )
                        f_verified = [
                            fc for fc in f_claims
                            if verify_quote(fc.get("exact_quote", ""), f_text)
                        ]
                        for fvc in f_verified:
                            fvc["source_id"] = f_sid
                            fvc["url"] = f_url
                        if f_verified:
                            self.ledger = group_and_update_ledger(
                                self.ledger,
                                f_verified,
                                self.sources_map,
                                sq.index,
                            )

        # i) Save checkpoint
        sq.completed = True
        save_notes(self.project_id, self.notes)
        save_ledger(self.project_id, self.ledger)
        # Store sources with text stripped from JSON disk record to conserve space
        sources_meta = [
            {k: v for k, v in s.items() if k != "text"}
            for s in self.sources
        ]
        save_sources(self.project_id, sources_meta)
        save_status(self.project_id, {
            "phase": "researching",
            "sq_index": sq.index,
            "dropped_quotes": self.dropped_quotes_count,
            "total_pages": self.total_pages_fetched,
        })
        update_index_entry(
            self.project_id,
            completed_sq=sq.index,
            total_sq=total_sqs,
            source_count=len(self.sources),
            status="researching",
        )

    def run_all(self, resume_from_sq: int = 1) -> None:
        """Run through all sub-questions sequentially."""
        total_sqs = len(self.plan.sub_questions)
        for i, sq in enumerate(self.plan.sub_questions, start=1):
            if i < resume_from_sq:
                continue
            if self._is_stopped():
                break
            self.run_subquestion(sq, i, total_sqs)

        if self._own_client and self.client:
            self.client.close()
