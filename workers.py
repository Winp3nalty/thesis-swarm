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
    """The JSON matched the schema but the content breaks the task's rules. `previous` is the rejected body."""

    def __init__(self, message: str, previous: str | None = None):
        super().__init__(message)
        self.previous = previous


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
        if n < 100:
            _, previous = _describe_schema_error(err)
            shown = f"\nYour previous body (extend it, keep what is good):\n{previous}\n" if previous else ""
            return (f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED: the body had only {n} words, "
                    f"but it needs at least 130. Rewrite the whole body and ADD about {140 - n} more words: "
                    "one more sentence on what the students hope to learn from the professor's topics, and one more "
                    "on how the visit can fit the professor's schedule. Do not repeat your previous text." + shown)
        return (f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED: the body had {n} words, but it must have at most 180. "
                "Shorten it to about 160 words.")
    if isinstance(err, SemanticError) and err.previous:
        bad = offending_sentences(err.previous)
        quoted = ("\nRewrite ONLY these sentences, so that they claim nothing about the students and give no date:\n"
                  + "\n".join(f'- "{s}"' for s in bad) + "\nKeep every other sentence as it is.\n") if bad else ""
        return (f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED: {str(err)[:600]}\n"
                f"Your previous body:\n{err.previous}\n{quoted}"
                "Answer again with the problems fixed. The body still needs at least 130 words.")
    return (f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED: {str(err)[:600]}\n"
            "Fix these problems and answer again.")


def _try_repair(repair, result, validator):
    """The repaired result if it is valid (schema and content rules), else None."""
    try:
        fixed = repair(result)
    except (ValidationError, ValueError):
        return None
    return fixed if fixed is not None and not validator(fixed) else None


def run_worker(system_prompt, user_prompt, response_model, validator=None, max_retries=2, repair=None, repair_after=2):
    """
    Call the model, retrying with feedback when the reply is invalid.
    Returns (result, info) where info = {attempts, first_schema_ok, first_semantic_ok}.
    Raises WorkerFailed when it is still invalid after max_retries retries.
    repair(result) may return a corrected result (or None); it is tried from attempt `repair_after` on, and is
    accepted only when the validator then finds no problem. info["repaired"] says whether code fixed the answer.
    """
    info = {"attempts": 0, "first_schema_ok": None, "first_semantic_ok": None,
            "first_problems": [], "attempt_log": [], "repaired": False}
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
            if repair and n >= repair_after:
                fixed = _try_repair(repair, result, validator)
                if fixed is not None:
                    info["attempt_log"].append({"attempt": n, "status": "repaired", "problems": [
                        "code removed the sentences that broke the rules and the rest passed all checks"],
                        "rejected_text": None, "response": fixed.model_dump()})
                    info["repaired"] = True
                    return fixed, info
            err = SemanticError("; ".join(problems), previous=getattr(result, "body", None))
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
        + f"\n\nReturn exactly {len(stops) - 1} leg(s), one between each consecutive pair of stops, in this exact order. "
        "Do not add a leg to a hotel or back to the start."
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
                          "our own projects", "our thesis", "currently exploring", "currently working",
                          "exploring similar", "our studies", "we are working on", "we have been working",
                          "our experience in", "our group has", "our team has")


_SELF = re.compile(r"\b(we|our|ours|us|the students|the group|the team|this group|our group|our team)\b")
_DOING = re.compile(r"\b(studying|researching|developing|building|working|investigating|designing|currently|"
                    r"(?:have|has) (?:been )?(?:studied|worked|developed|built|designed|researched|published|conducted))\b")
_STUDY_AT = re.compile(r"\bstud(?:y|ies|ying)(?: engineering| robotics)? at\b")


_MONTHS = ("january|february|march|april|june|july|august|september|october|november|december")
_TIMING = re.compile(rf"\b(?:upcoming|coming|next|this|following)\s+(?:week|weeks|month|months|spring|summer|autumn|fall|winter|"
                     rf"semester|year|weekend)\b|\b(?:{_MONTHS})\b|\b20\d\d\b|\b(?:tomorrow|next week|in (?:two|three|a few) weeks)\b")


def claim_sentences(body: str) -> list[str]:
    """Sentences where the student group is the subject of work it is doing or has done ("Our group is studying
    X", "we have been working on Y"). The students may only say that they visit and want to learn, so this is
    judged per sentence, not by a word list: a phrase list misses every new wording the model finds."""
    found = []
    for sent in re.split(r"(?<=[.!?])\s+", body.strip()):
        low = sent.lower()
        if _SELF.search(low) and _DOING.search(low) and not _STUDY_AT.search(low):
            found.append(sent.strip())
    return found


def offending_sentences(body: str) -> list[str]:
    """The sentences of an email body that break the claim or timing rules, to quote them in the retry prompt."""
    low_body = _norm(body)
    bad = list(claim_sentences(body))
    for sent in re.split(r"(?<=[.!?])\s+", body.strip()):
        low = _norm(sent)
        if sent.strip() in bad:
            continue
        if any(re.search(rf"\b{re.escape(p)}\b", low) for p in INVENTED_CLAIM_PHRASES) or _TIMING.search(low):
            bad.append(sent.strip())
    return bad if low_body else []


def repair_outreach(email: OutreachEmail) -> OutreachEmail | None:
    """Delete the sentences that break the claim or timing rules. None when nothing can be removed. The caller
    validates the result again, so a body that is now too short or lost its topic is not accepted."""
    bad = set(offending_sentences(email.body))
    if not bad:
        return None
    paragraphs = []
    for para in email.body.split("\n\n"):
        kept = [x for x in re.split(r"(?<=[.!?])\s+", para.strip()) if x and x.strip() not in bad]
        if kept:
            paragraphs.append(" ".join(kept))
    return OutreachEmail(
        subject=email.subject, greeting=email.greeting, body="\n\n".join(paragraphs), sign_off=email.sign_off)


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
    # whole words only: "your research" must not match "our research"
    claims = [p for p in INVENTED_CLAIM_PHRASES if re.search(rf"\b{re.escape(p)}\b", body)]
    if claims:
        problems.append(f"do not claim any project or result of the student group (found '{claims[0]}'); "
                        "say only that the students are visiting and want to learn")
    else:
        sentences = claim_sentences(email.body)
        if sentences:
            problems.append("do not claim any project or work of the student group; this sentence does ("
                            f"'{sentences[0][:120]}'). Say only that the students are visiting and want to learn")
    if not contact.get("trip_dates", "").strip():
        timing = _TIMING.search(body)
        if timing:  # no dates were given, so any time statement is invented
            problems.append(f"do not state when the visit happens (found '{timing.group(0)}'); no travel dates "
                            "are given. Say only that the students are visiting Shenzhen")
    if "30" not in email.body and "thirty" not in body:
        problems.append("the body must ask for a 30-minute tour")
    return problems


def agent_outreach(contact: dict, max_retries: int = 3):
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
        "Use the word \"our\" only in \"our group\" and \"our visit\". Never write \"our studies\", \"our work\" or \"our research\".\n"
        "For the interest sentence use this pattern: \"We would like to learn how your work on <topic> is done in practice.\"\n"
        + ("" if contact.get("trip_dates") else "No travel dates are known: do not mention when the visit happens.\n")
        +
        f"Paragraph 1 MUST contain the words \"{contact['institution']}\".\n"
        "Length: write about 165 words in three paragraphs; the body must have at least 130 words.\n"
    )
    return run_worker(system_prompt, user_prompt, OutreachEmail,
                      lambda email: validate_outreach(email, contact), max_retries, repair=repair_outreach)
