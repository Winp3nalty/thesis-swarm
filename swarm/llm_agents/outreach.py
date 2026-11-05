"""
Outreach agent: one email per professor asking for a 30-minute lab tour (persona and rules in
prompts/outreach/SOUL.md).

The checks here are plain code: institution, topics, greeting, sender, claims about the student group, invented
timing and length. After two rejections repair_outreach() deletes the rule-breaking sentences itself.
"""
from __future__ import annotations

import re

from swarm.core.soul_loader import build_system_prompt
from swarm.core.worker_schemas import OutreachEmail
from swarm.llm_agents.retry import run_worker
from swarm.llm_agents.textcheck import _key_words, _norm

_GENERIC_WORDS = {"university", "institute", "technology", "college", "science", "school", "state"}


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
                      lambda email: validate_outreach(email, contact), max_retries, repair=repair_outreach,
                      offending=offending_sentences)
