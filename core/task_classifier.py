"""
Autonomous Task Intent Classifier.

Determines whether a user prompt represents:
  - 'chat': General conversation, questions, reasoning, or explanations without building code.
  - 'code': Pure programming task to design, build, debug, or scaffold an application/script.
  - 'both': Hybrid task requesting both an explanation/discussion AND a full code implementation.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from core.ollama_client import OllamaClient

TaskType = Literal["chat", "code", "both"]

# Regex patterns identifying clear code requests
_CODE_ACTION_VERBS = (
    r"(build|create|code|program|make|develop|implement|generate|write|scaffold|refactor|debug|fix)"
)
_CODE_NOUNS = (
    r"(app|application|game|script|program|project|bot|website|api|server|"
    r"service|tool|cli|crawler|scraper|gui|frontend|backend|flask|django|"
    r"fastapi|pyside|pyqt|tkinter|react|vue|node|express|database|algorithm|"
    r"class|module|function|library|package)"
)

_CODE_PATTERN = re.compile(
    rf"\b{_CODE_ACTION_VERBS}\b.*?\b{_CODE_NOUNS}\b",
    re.IGNORECASE | re.DOTALL,
)

_CODE_EXPLICIT_PHRASES = [
    "write a python script",
    "write code",
    "code a ",
    "build an app",
    "build a game",
    "create an app",
    "create a game",
    "write a program",
    "code this",
    "implement a ",
    "debug this",
    "fix this error",
    "traceback (most recent call last)",
    "syntaxerror",
    "nameerror",
]

# Regex patterns identifying explanation / discussion
_EXPLANATION_PATTERN = re.compile(
    r"\b(explain|why|how does|what is|tell me about|compare|pros and cons|difference between|describe|walk me through)\b",
    re.IGNORECASE,
)

# Hybrid intent patterns: asking for explanation AND implementation
_HYBRID_PATTERNS = [
    re.compile(r"\b(explain|what is|how does)\b.*?\b(and|then|also)\b.*?\b(build|code|implement|write a program|create)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(build|code|implement|create)\b.*?\b(and|then|also)\b.*?\b(explain|walk me through|give an overview)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(explain)\b.*?\b(with a complete implementation|with a working project|with full code)\b", re.IGNORECASE),
]


_PROGRAMMING_KEYWORD_PATTERN = re.compile(
    r"\b(code|coding|program|programming|script|scripting|app|application|software|"
    r"python|javascript|typescript|c\+\+|java|rust|go|golang|html|css|php|ruby|swift|kotlin|"
    r"sql|database|postgres|sqlite|mysql|mongodb|redis|"
    r"api|rest|endpoint|server|backend|frontend|framework|django|flask|fastapi|express|react|vue|"
    r"function|class|method|variable|loop|array|dict|dictionary|json|xml|regex|"
    r"algorithm|data structure|debug|debugging|bug|error|exception|traceback|"
    r"git|github|docker|terminal|cli|gui|pyside|pyqt|tkinter)\b",
    re.IGNORECASE,
)


def classify_heuristic(prompt: str) -> TaskType | None:
    """
    Fast rule-based intent classification (resolves in < 0.1ms).
    Returns 'code', 'chat', 'both', or None if truly ambiguous.
    """
    clean = prompt.strip().lower()

    if not clean:
        return "chat"

    # Fast-path 1: Zero programming keywords & zero code markers -> 100% conversational chat
    has_code_markers = bool("```" in prompt or "def " in prompt or "class " in prompt or "import " in prompt)
    has_prog_keywords = bool(_PROGRAMMING_KEYWORD_PATTERN.search(clean))
    has_code_action = bool(_CODE_PATTERN.search(clean) or any(phrase in clean for phrase in _CODE_EXPLICIT_PHRASES))

    if not has_prog_keywords and not has_code_markers and not has_code_action:
        return "chat"

    # Fast-path 2: Check for hybrid intent (wants explanation AND implementation)
    for p in _HYBRID_PATTERNS:
        if p.search(clean):
            return "both"

    # Greetings and general conversation
    if re.search(r"\b(hi|hello|hey|good morning|how are you|who are you|what can you do|tell me a joke|thanks|thank you|bye)\b", clean):
        if not has_code_action and not has_code_markers:
            return "chat"

    has_explanation = bool(_EXPLANATION_PATTERN.search(clean))
    is_general_question = bool(re.search(r"^(what|why|how|who|where|when|can you|is |are |tell me|which)\b", clean))

    if has_code_action and (has_explanation or is_general_question):
        return "both"
    elif has_code_action:
        return "code"
    elif has_explanation or is_general_question:
        return "chat"

    # Code markers (backticks, def, class, import)
    if has_code_markers:
        if any(w in clean for w in ["explain", "why", "what is", "how does"]):
            return "chat"
        return "code"

    # Short conversational phrases with programming words (e.g. "thanks for the code", "what is python")
    if len(clean.split()) <= 8 and not has_code_action:
        return "chat"

    return "chat"


def classify_with_llm(
    ollama_url: str,
    model: str,
    prompt: str,
    num_ctx: int = 8192,
    client: OllamaClient | None = None,
) -> TaskType:
    """
    Fast LLM-based fallback classification when heuristic is ambiguous.
    """
    from core.storage import get_configured_threads
    own_client = False
    if client is None:
        client = OllamaClient(ollama_url)
        own_client = True

    system_prompt = (
        "You are an intent classifier. Categorize the user's request as one of:\n"
        "- 'code': The user wants a script, app, project, or code built or fixed.\n"
        "- 'chat': The user wants general conversation, explanation, or advice.\n"
        "- 'both': The user wants an explanation/advice AND code/project created.\n"
        "Respond with ONLY one word: code, chat, or both."
    )

    try:
        chunks = []
        opts: dict[str, Any] = {
            "num_predict": 4,
            "temperature": 0.0,
            "num_ctx": num_ctx,
        }
        threads = get_configured_threads()
        if threads is not None:
            opts["num_thread"] = threads

        for chunk in client.chat_stream(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt[:300]},
            ],
            options=opts,
        ):
            if c := chunk.get("message", {}).get("content", ""):
                chunks.append(c)
            if chunk.get("done", False):
                break

        res = "".join(chunks).strip().lower()
        if "both" in res:
            return "both"
        elif "code" in res:
            return "code"
        elif "chat" in res:
            return "chat"
    except Exception:
        pass
    finally:
        if own_client:
            client.close()

    return "chat"


def classify_task(
    prompt: str,
    ollama_url: str = "",
    model: str = "",
    num_ctx: int = 8192,
    client: OllamaClient | None = None,
) -> TaskType:
    """
    Autonomously decide whether the task is 'chat', 'code', or 'both'.
    Uses fast heuristics first, falling back to LLM for ambiguous prompts.
    """
    verdict = classify_heuristic(prompt)
    if verdict:
        return verdict

    if ollama_url and model:
        return classify_with_llm(ollama_url, model, prompt, num_ctx=num_ctx, client=client)

    return "chat"
