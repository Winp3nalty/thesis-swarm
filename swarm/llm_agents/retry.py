"""
The retry loop that every worker agent shares (Transit, Outreach).

The model is called with a Pydantic schema; when the reply is invalid, the reason is fed back as plain text
and the model is called again. run_worker() also reports whether the FIRST attempt was valid, which is the number
the Week 3 pass metric ("100% valid JSON") is about. An agent may pass a repair function: code that fixes a stubborn
answer after a few rejections (see outreach.py).
"""
from __future__ import annotations

import re

from pydantic import ValidationError

from swarm.llm_agents import client


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


def _retry_feedback(err: Exception, offending=None) -> str:
    """Tell the model exactly what was wrong; for a short body, give the word shortfall.
    offending(body) may return the rule-breaking sentences of a body, which are then quoted back."""
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
        bad = offending(err.previous) if offending else []
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


def run_worker(system_prompt, user_prompt, response_model, validator=None, max_retries=2, repair=None, repair_after=2,
               offending=None):
    """
    Call the model, retrying with feedback when the reply is invalid.
    Returns (result, info) where info = {attempts, first_schema_ok, first_semantic_ok}.
    Raises WorkerFailed when it is still invalid after max_retries retries.
    repair(result) may return a corrected result (or None); it is tried from attempt `repair_after` on, and is
    accepted only when the validator then finds no problem. info["repaired"] says whether code fixed the answer.
    offending(body) lets the retry text quote the sentences that broke the rules.
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
            result = client.call_structured_llm(system_prompt, user_prompt + feedback, response_model,
                                                  temperature=temperature)
        except (ValidationError, client.StructuredOutputError) as e:
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
        feedback = _retry_feedback(err, offending)
