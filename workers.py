"""
Week 3 worker agents: Transit and Outreach.

Both use the same pattern as the main swarm: persona and rules from agents/<name>/SOUL.md,
output constrained by a Pydantic schema, and a retry loop that feeds the rejection reason
back to the model. run_worker() also reports whether the FIRST attempt was valid, which is
the number the Week 3 pass metric ("100% valid JSON") is about.
"""
from __future__ import annotations

import re

from pydantic import ValidationError

import run_loop
from soul_loader import build_system_prompt
from worker_schemas import OutreachEmail, TransitPlan


class SemanticError(ValueError):
    """The JSON matched the schema but the content breaks the task's rules."""


class WorkerFailed(RuntimeError):
    def __init__(self, info: dict, last_error: Exception):
        super().__init__(f"worker failed after {info['attempts']} attempt(s): {str(last_error)[:300]}")
        self.info = info
        self.last_error = last_error


def _describe_schema_error(e: Exception):
    """Short readable problem list, plus the rejected text when pydantic kept it."""
    if isinstance(e, ValidationError):
        errs = e.errors()
        problems = [f"{'.'.join(str(p) for p in x['loc']) or 'reply'}: {x['msg']}" for x in errs]
        rejected = next((x["input"] for x in errs if isinstance(x.get("input"), str) and len(x["input"]) > 40), None)
        return problems, rejected
    return [str(e)[:300]], None


def _retry_feedback(err: Exception) -> str:
    """Tell the model exactly what was wrong; for a short body, give the word shortfall."""
    m = re.search(r"body has (\d+) words", str(err))
    if m:
        n = int(m.group(1))
        if n < 120:
            return (f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED: the body had only {n} words, "
                    f"but it needs at least 140. Rewrite the whole body and ADD about {150 - n} more words: "
                    "expand the paragraph about the professor's research and the paragraph with the request. "
                    "Do not repeat your previous text.")
        return (f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED: the body had {n} words, but it must have at most 180. "
                "Shorten it to about 160 words.")
    return (f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED: {str(err)[:600]}\n"
            "Fix these problems and answer again.")


def run_worker(system_prompt, user_prompt, response_model, validator=None, max_retries=2):
    """
    Call the model, retrying with feedback when the reply is invalid.
    Returns (result, info) where info = {attempts, first_schema_ok, first_semantic_ok}.
    Raises WorkerFailed when it is still invalid after max_retries retries.
    """
    info = {"attempts": 0, "first_schema_ok": None, "first_semantic_ok": None,
            "first_problems": [], "attempt_log": []}
    feedback = ""
    while True:
        info["attempts"] += 1
        n = info["attempts"]
        first = n == 1
        temperature = 0.2 if first else 0.7  # a little more variety on retries
        try:
            result = run_loop.call_structured_llm(system_prompt, user_prompt + feedback, response_model,
                                                  temperature=temperature)
        except (ValidationError, run_loop.StructuredOutputError) as e:
            problems, rejected = _describe_schema_error(e)
            info["attempt_log"].append({"attempt": n, "status": "schema_error", "problems": problems,
                                        "rejected_text": rejected, "response": None})
            if first:
                info["first_schema_ok"] = False
                info["first_semantic_ok"] = False
                info["first_problems"] = problems
            err = e
        else:
            problems = validator(result) if validator else []
            info["attempt_log"].append({"attempt": n, "status": "content_problems" if problems else "ok",
                                        "problems": problems, "rejected_text": None,
                                        "response": result.model_dump()})
            if first:
                info["first_schema_ok"] = True
                info["first_semantic_ok"] = not problems
                info["first_problems"] = problems
            if not problems:
                return result, info
            err = SemanticError("; ".join(problems))
        if info["attempts"] > max_retries:
            raise WorkerFailed(info, err)
        feedback = _retry_feedback(err)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


_GENERIC_WORDS = {"university", "institute", "technology", "college", "science", "school", "state"}


def _key_words(text: str, stop: set[str] | None = None) -> set[str]:
    return {w for w in re.findall(r"[a-z]{5,}", text.lower()) if w not in (stop or set())}


# ==========================================
# TRANSIT AGENT
# ==========================================
def validate_transit(plan: TransitPlan, stops: list[str], drive_minutes: list | None = None) -> list[str]:
    problems = []
    if len(plan.legs) != len(stops) - 1:
        problems.append(f"expected {len(stops) - 1} legs (one per consecutive pair), got {len(plan.legs)}")
    for i, leg in enumerate(plan.legs[: len(stops) - 1]):
        if _norm(leg.from_stop) != _norm(stops[i]):
            problems.append(f"leg {i + 1} must start at '{stops[i]}'")
        if _norm(leg.to_stop) != _norm(stops[i + 1]):
            problems.append(f"leg {i + 1} must end at '{stops[i + 1]}'")
        d = drive_minutes[i] if drive_minutes and i < len(drive_minutes) else None
        if d is not None:  # a measured driving time exists: a trip far outside it is a made-up number
            lo, hi = max(2.0, 0.4 * d), 3 * d + 20
            if not lo <= leg.minutes <= hi:
                problems.append(f"leg {i + 1} ({stops[i]} to {stops[i + 1]}) takes {leg.minutes} min, but driving takes "
                                f"about {d:.0f} min; give a time between {lo:.0f} and {hi:.0f} min")
    return problems


def agent_transit(city: str, stops: list[str], day: int = 1, max_retries: int = 2,
                  drive_minutes: list | None = None):
    if len(stops) < 2:
        raise ValueError("need at least two stops")
    print(f"  [Transit Agent] Routing {len(stops)} stops in {city}...")
    system_prompt = build_system_prompt("transit")
    user_prompt = (
        f"City: {city}\nDay: {day}\nOrdered stops:\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(stops))
        + "\n\nReturn one leg between each consecutive pair of stops, in this exact order."
    )
    known = [(i, d) for i, d in enumerate(drive_minutes or []) if d is not None]
    if known:
        user_prompt += ("\nMeasured driving time (OpenStreetMap routing, no traffic), use it to judge the trip time:\n"
                        + "\n".join(f"- leg {i + 1}: about {d:.0f} min by car" for i, d in known) + "\n")
    return run_worker(system_prompt, user_prompt, TransitPlan,
                      lambda plan: validate_transit(plan, stops, drive_minutes), max_retries)


# ==========================================
# OUTREACH AGENT
# ==========================================
INVENTED_CLAIM_PHRASES = ("our project", "our current", "currently developing", "our research", "our work on",
                          "we are developing", "we have developed", "we are building", "our team has built",
                          "our own projects", "our thesis")


def validate_outreach(email: OutreachEmail, contact: dict) -> list[str]:
    problems = []
    body = _norm(email.body)
    inst_words = _key_words(contact["institution"], _GENERIC_WORDS) or {_norm(contact["institution"])}
    inst_words |= {_norm(a) for a in contact.get("aliases", [])}  # e.g. "SUSTech"
    if not any(w in body for w in inst_words):
        problems.append(f"the body must mention the institution '{contact['institution']}'")
    topic_words = set().union(*(_key_words(t) for t in contact["topics"]))
    if not any(w in body for w in topic_words):
        problems.append("the body must refer to at least one of the professor's research topics")
    # Chinese names are written family-name-first ("Zhou Mingyu"), so accept any word of the name.
    name_words = [_norm(w) for w in contact["name"].replace(".", " ").split() if _norm(w) not in ("prof", "professor", "dr")]
    if not any(w in _norm(email.greeting) for w in name_words):
        problems.append(f"the greeting must address the professor by name ('{contact['name']}')")
    sender = contact.get("sender_name", "").strip()
    if sender and not any(_norm(w) in _norm(email.sign_off) for w in sender.split()):
        problems.append(f"the sign-off must include the sender's name ('{sender}')")
    univ = contact.get("sender_university", "").strip()
    if univ and not any(w in body for w in (_key_words(univ, _GENERIC_WORDS) or {_norm(univ)})):
        problems.append(f"the body must say that the group comes from '{univ}'")
    claims = [p for p in INVENTED_CLAIM_PHRASES if p in body]
    if claims:
        problems.append(f"do not claim any project or result of the student group (found '{claims[0]}'); "
                        "say only that the students are visiting and want to learn")
    if "30" not in email.body and "thirty" not in body:
        problems.append("the body must ask for a 30-minute tour")
    return problems


def agent_outreach(contact: dict, max_retries: int = 2):
    print(f"  [Outreach Agent] Drafting email to {contact['name']} ({contact['institution']})...")
    system_prompt = build_system_prompt("outreach")
    user_prompt = (
        f"Professor: {contact['name']}\n"
        f"Institution: {contact['institution']}\n"
        f"The body MUST name the institution exactly as written: {contact['institution']}.\n"
        f"The greeting MUST use the professor's name exactly as written above (e.g. 'Dear {contact['name']},').\n"
        f"Recent research topics: {'; '.join(contact['topics'])}\n"
        f"Expedition: a small group of engineering students visiting {contact['city']}.\n"
        + (f"The group studies at {contact['sender_university']}; the body must say so.\n" if contact.get("sender_university") else "")
        + (f"Planned travel time: {contact['trip_dates']}.\n" if contact.get("trip_dates") else "")
        + (f"The sender is {contact['sender_name']}; the sign_off must be a closing phrase followed by this name.\n" if contact.get("sender_name") else "")
        +
        "Never claim a project, research or result of the student group; the group only wants to learn.\n"
        f"Paragraph 1 MUST contain the words \"{contact['institution']}\".\n"
        "Length: write about 165 words in three paragraphs; the body must have at least 150 words.\n"
    )
    return run_worker(system_prompt, user_prompt, OutreachEmail,
                      lambda email: validate_outreach(email, contact), max_retries)
