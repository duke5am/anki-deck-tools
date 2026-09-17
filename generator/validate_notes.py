#!/usr/bin/env python3
"""validate_notes.py -- a quality linter for spaced-repetition source notes.

WHY THIS EXISTS
---------------
Most shared Anki decks are bad in the same handful of ways: cards carry five
facts at once, the prompt cannot be answered without extra context, the card is
worded negatively, the answer is a paragraph, two cards test the same thing, or
nobody wrote down where the fact came from.

This script encodes those failures as named rules. It reads the same source
notes that ``build_deck.py`` consumes and refuses to let a bad note set become a
deck. Every finding names the offending note id and the rule id, so you can fix
exactly the card that is wrong.

USAGE
-----
    python3 validate_notes.py decks/http-status-codes.notes.json
    python3 validate_notes.py notes.yaml --max-answer-chars 160
    python3 validate_notes.py notes.json --json > findings.json
    python3 validate_notes.py --list-rules

Exit status: 0 = clean, 1 = violations found, 2 = could not read/parse input.

RULES
-----
R001 one-fact-per-card            the answer states more than one independent fact
R002 unambiguous-prompt           the prompt cannot stand alone as a question
R003 no-negative-wording          the card is phrased as a negative / "except" item
R004 answer-too-long              the answer exceeds the configured character limit
R005 duplicate-or-near-duplicate  two notes ask effectively the same thing
R006 orphan-card                  no topic, or a topic that is not declared
R007 missing-source-attribution   the note claims a source but gives none
R008 empty-required-field         a field the card needs is blank
R009 malformed-cloze              cloze markers are missing, empty or overlapping
R010 duplicate-or-missing-note-id ids are absent or reused (breaks regeneration)
R011 invalid-tag                  a tag contains a space or is otherwise unusable
R012 prompt-leaks-answer          the answer is already visible in the prompt

The one-fact, ambiguity, duplication and leak checks are heuristics, not proofs.
They are tuned to be quiet on well-written cards and loud on the failure modes
above. Read the rule text and decide; do not treat a clean run as a guarantee of
pedagogical quality.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from dataclasses import dataclass, asdict

# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

RULES = {
    "R001": "one-fact-per-card",
    "R002": "unambiguous-prompt",
    "R003": "no-negative-wording",
    "R004": "answer-too-long",
    "R005": "duplicate-or-near-duplicate",
    "R006": "orphan-card",
    "R007": "missing-source-attribution",
    "R008": "empty-required-field",
    "R009": "malformed-cloze",
    "R010": "duplicate-or-missing-note-id",
    "R011": "invalid-tag",
    "R012": "prompt-leaks-answer",
}

RULE_HELP = {
    "R001": "Split into one card per fact, or use a cloze card with several deletions.",
    "R002": "Rewrite the prompt so it names its own subject; do not start with it/this/that.",
    "R003": "Ask for the positive fact instead of 'which is NOT'. Negation doubles the error rate.",
    "R004": "Move the detail to a second card, or raise --max-answer-chars deliberately.",
    "R005": "Delete one of the pair, or make the difference between them the thing being asked.",
    "R006": "Give the note a topic that appears in the deck's \"topics\" list.",
    "R007": "Add the source, or set \"source_claimed\": false if the note really needs none.",
    "R008": "Fill in the field, or remove the note.",
    "R009": "Use {{c1::...}} with a non-empty deletion for every cloze number.",
    "R010": "Every note needs a stable, unique id; ids are what keep scheduling history.",
    "R011": "Tags may not contain spaces; use hyphens (topic tags are generated for you).",
    "R012": "Ask the question from the other direction so the answer is not in the prompt.",
}

DEFAULT_CONFIG = {
    # R004: a "wall of text" answer. 220 characters is roughly two short lines.
    "max_answer_chars": 220,
    # R002: a prompt shorter than this cannot identify its own subject.
    "min_prompt_chars": 12,
    # R002: prompts longer than this are usually two questions in one.
    "max_prompt_chars": 300,
    # R005: similarity ratio at or above this counts as a near duplicate.
    "duplicate_threshold": 0.90,
    # R007: when true, a note with no source at all is a violation unless it
    # explicitly sets "source_claimed": false.
    "require_source": True,
    # R012: fraction of the answer's content words that may appear in the prompt.
    "leak_threshold": 0.80,
    # R009: a cloze note with more deletions than this is really several cards.
    "max_cloze_deletions": 5,
    # R011: tags containing any of these are rejected.
    "forbidden_tag_chars": " \t\n",
}

# Wording that turns a card into a negative item. These are the classic phrasings
# and also the reason exam-style "except" questions are poor SRS material.
NEGATIVE_PATTERNS = [
    r"\ball of the following except\b",
    r"\bwhich of the following\b[^?]{0,40}\b(not|except|incorrect|false|least)\b",
    r"\bwhich (?:one )?is (?:not|false|incorrect)\b",
    r"\bwhich .{0,30}\bis not\b",
    r"\bnot true\b",
    r"\bincorrect\b",
    r"\buntrue\b",
    r"\bexcept\b",
    r"\bexcludes?\b",
    r"\bis false\b",
    r"\bare false\b",
    r"\bdoes not\b.{0,30}\?$",
    r"\bcannot be\b",
    r"\bnever\b",
    r"\bnone of the\b",
]

# Phrases that point at context the card does not carry.
DANGLING_PHRASES = [
    r"\bthe above\b",
    r"\bthe below\b",
    r"\bas discussed\b",
    r"\bas mentioned\b",
    r"\bas shown\b",
    r"\bpreviously\b",
    r"\bearlier\b",
    r"\bthe following\b",
    r"\bthis section\b",
    r"\bthat section\b",
    r"\bthe previous\b",
    r"\bin the last card\b",
]

# A prompt may not open with a bare reference word: nothing tells the reader
# what "it" or "that" refers to once the card is out of its original context.
LEADING_REFERENCE = re.compile(
    r"^(?:it|its|this|that|these|those|they|them|their|he|she|his|her|such|the same|both|either|neither)\b",
    re.IGNORECASE,
)

# Cue openings that make a prompt self-contained without a question mark.
CUE_OPENINGS = re.compile(
    r"^(?:define|name|list|state|give|explain|describe|translate|complete|convert|identify|"
    r"contrast|compare|rewrite|write|recall|fill in|supply|summarise|summarize)\b",
    re.IGNORECASE,
)

CLOZE_RE = re.compile(r"\{\{c(\d+)::(.*?)(?:::(.*?))?\}\}", re.DOTALL)

STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "is",
    "are", "was", "were", "be", "been", "being", "it", "its", "as", "by",
    "with", "from", "that", "this", "these", "those", "which", "what", "when",
    "where", "who", "whom", "whose", "how", "why", "does", "do", "did", "done",
    "can", "could", "will", "would", "should", "must", "may", "might", "than",
    "then", "there", "here", "into", "over", "under", "about", "after",
    "before", "between", "not", "no", "yes", "if", "so", "such", "only",
    "also", "very", "more", "most", "less", "least", "each", "every", "any",
    "all", "some", "one", "two", "you", "your", "we", "our", "they", "them",
    "their", "he", "she", "his", "her", "have", "has", "had", "having",
}

ABBREVIATIONS = (
    "e.g", "i.e", "etc", "vs", "cf", "approx", "resp", "al", "fig", "sec",
    "art", "ca", "dr", "Inc", "Ltd", "Mr", "Ms", "St", "No", "vol", "pp",
)


@dataclass
class Finding:
    note_id: str
    rule: str
    rule_name: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)

    def format_line(self) -> str:
        return f"{self.note_id}: {self.rule} {self.rule_name}: {self.message}"


@dataclass
class Advisory:
    """Something worth looking at that is not a rule violation.

    Currently only produced by R005: a group of cards that share a near-identical
    prompt but ask for different answers. That shape is normal for a deck of
    codes, so it does not fail the build, but it is where interference between
    similar cards appears, so it is reported rather than hidden.
    """

    rule: str
    rule_name: str
    message: str
    note_ids: list

    def as_dict(self) -> dict:
        return asdict(self)

    def format_line(self) -> str:
        shown = ", ".join(self.note_ids[:8])
        if len(self.note_ids) > 8:
            shown += f", +{len(self.note_ids) - 8} more"
        return f"ADVISORY {self.rule} {self.rule_name}: {self.message} [{shown}]"


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def load_document(path: str) -> dict:
    """Read a notes document from JSON or YAML.

    Accepts either a full document (``{"deck": ..., "topics": [...], "notes": [...]}``)
    or a bare list of notes, which is handy for linting a fragment.
    """
    with open(path, "r", encoding="utf-8") as handle:
        raw = handle.read()

    data = None
    ext = os.path.splitext(path)[1].lower()
    if ext in (".yaml", ".yml"):
        data = _load_yaml(raw, path)
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as first_error:
            # A .json file that is really YAML is a common mistake; try YAML
            # before giving up, then report the original JSON error.
            try:
                data = _load_yaml(raw, path)
            except Exception:
                raise ValueError(f"{path}: not valid JSON ({first_error.msg} at line {first_error.lineno})")

    if isinstance(data, list):
        data = {"notes": data}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object with a \"notes\" list")
    if "notes" not in data:
        raise ValueError(f"{path}: no \"notes\" key found")
    if not isinstance(data["notes"], list):
        raise ValueError(f"{path}: \"notes\" must be a list")
    return data


def _load_yaml(raw: str, path: str):
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ValueError(
            f"{path}: YAML input needs PyYAML (pip install pyyaml), or convert the file to JSON"
        ) from exc
    return yaml.safe_load(raw)


def note_fields(note: dict) -> dict:
    """Return the addressable text fields of a note, whatever its type.

    A ``"reverse": true`` note shows its back as the prompt and its front as the
    answer, so prompt-side rules are applied to the side the learner actually
    sees first.
    """
    kind = (note.get("type") or "basic").strip().lower()
    if kind == "cloze":
        return {
            "prompt": str(note.get("text", "") or ""),
            "answer": _cloze_answers(str(note.get("text", "") or "")),
            "extra": str(note.get("extra", "") or ""),
        }
    front = str(note.get("front", "") or "")
    back = str(note.get("back", "") or "")
    if kind == "basic" and note.get("reverse") is True:
        front, back = back, front
    return {
        "prompt": front,
        "answer": back,
        "extra": str(note.get("extra", "") or ""),
    }


def _cloze_answers(text: str) -> str:
    """The text of every cloze deletion, joined; this is what has to be recalled."""
    return " / ".join(match.group(2).strip() for match in CLOZE_RE.finditer(text))


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------


def normalise(text: str) -> str:
    """Lowercase, drop cloze markers/punctuation, collapse whitespace."""
    text = CLOZE_RE.sub(" __blank__ ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9\-]*", text.lower())
    return {w for w in words if len(w) >= 3 and w not in STOPWORDS}


def split_sentences(text: str) -> list[str]:
    """Split on sentence enders, ignoring decimals, version numbers and abbreviations."""
    # Protect runs of digits joined by dots first: 2.1, 127.0.0.1, 15.5.2.
    protected = re.sub(
        r"\b\d+(?:\.\d+)+\b", lambda match: match.group(0).replace(".", "<DOT>"), text
    )
    for abbr in ABBREVIATIONS:
        protected = re.sub(rf"\b{re.escape(abbr)}\.", abbr.replace(".", "") + "<DOT>", protected)
    protected = re.sub(r"\b([A-Z])\.", r"\1<DOT>", protected)
    parts = re.split(r"(?<=[.!?])\s+|\n+", protected)
    return [p.replace("<DOT>", ".").strip() for p in parts if p.strip()]


def has_enumerated_list(text: str) -> bool:
    """True when the answer enumerates items ('1.', '2)', '- ', bullet)."""
    if re.search(r"(?:^|\s)\d+[.)]\s", text):
        return True
    if re.search(r"(?:^|\n)\s*[-*\u2022]\s+", text):
        return True
    return False


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalise(a), normalise(b)).ratio()


# --------------------------------------------------------------------------
# Individual rules
# --------------------------------------------------------------------------


def check_one_fact(note: dict, config: dict) -> list[Finding]:
    """R001 -- the answer should state exactly one thing."""
    fields = note_fields(note)
    answer = fields["answer"]
    if not answer.strip():
        return []  # R008 reports emptiness
    findings = []

    clauses = [s for s in split_sentences(answer) if len(s.split()) >= 4]
    if len(clauses) >= 2:
        findings.append(
            _f(note, "R001", f"answer states {len(clauses)} separate sentences, so it is more than one fact")
        )
    elif has_enumerated_list(answer):
        findings.append(_f(note, "R001", "answer is an enumerated list, which is several facts on one card"))
    elif answer.count(";") >= 1:
        findings.append(_f(note, "R001", "answer joins separate clauses with a semicolon, which is several facts"))
    return findings


def check_unambiguous_prompt(note: dict, config: dict) -> list[Finding]:
    """R002 -- the prompt must identify its own subject, out of context."""
    kind = (note.get("type") or "basic").strip().lower()
    if kind == "cloze":
        return []  # a cloze prompt carries its own context by construction

    prompt = note_fields(note)["prompt"]
    if not prompt.strip():
        return []  # R008
    findings = []

    stripped = prompt.strip()
    if len(stripped) < config["min_prompt_chars"]:
        findings.append(
            _f(note, "R002", f"prompt is only {len(stripped)} characters, too short to be self-contained")
        )
    if len(stripped) > config["max_prompt_chars"]:
        findings.append(
            _f(note, "R002", f"prompt is {len(stripped)} characters; that is usually two questions in one")
        )

    for pattern in DANGLING_PHRASES:
        match = re.search(pattern, stripped, re.IGNORECASE)
        if match:
            findings.append(
                _f(note, "R002", "prompt refers to context it does not carry: " + repr(match.group(0)))
            )
            break

    if LEADING_REFERENCE.match(stripped):
        findings.append(
            _f(note, "R002", "prompt opens with a bare reference word (it/this/that/...), so it needs outside context")
        )

    if not (stripped.endswith("?") or CUE_OPENINGS.match(stripped)):
        findings.append(
            _f(note, "R002", "prompt is not question-shaped: end it with '?' or open with a cue such as 'Name' or 'Define'")
        )
    return findings


def check_negative_wording(note: dict, config: dict) -> list[Finding]:
    """R003 -- negatively worded cards are unreliable; ask for the positive fact."""
    fields = note_fields(note)
    haystack = fields["prompt"]
    if not haystack.strip():
        return []
    if note.get("allow_negative") is True:
        return []
    for pattern in NEGATIVE_PATTERNS:
        match = re.search(pattern, haystack, re.IGNORECASE)
        if match:
            return [
                _f(note, "R003", f"negative wording in prompt: {match.group(0)!r}")
            ]
    return []


def check_answer_length(note: dict, config: dict) -> list[Finding]:
    """R004 -- a wall of text is not a recallable answer."""
    limit = config["max_answer_chars"]
    fields = note_fields(note)
    kind = (note.get("type") or "basic").strip().lower()
    findings = []

    if kind == "cloze":
        for match in CLOZE_RE.finditer(fields["prompt"]):
            deletion = match.group(2).strip()
            if len(deletion) > limit:
                findings.append(
                    _f(note, "R004", f"cloze deletion {len(deletion)} characters long exceeds the {limit} limit")
                )
            if len(deletion.split()) >= 12:
                findings.append(
                    _f(note, "R004", f"cloze deletion is {len(deletion.split())} words long; that is prose, not an answer")
                )
        return findings

    answer = fields["answer"].strip()
    if len(answer) > limit:
        findings.append(_f(note, "R004", f"answer is {len(answer)} characters, over the {limit} limit"))
    if len(answer.split()) >= 45:
        findings.append(_f(note, "R004", f"answer is {len(answer.split())} words long"))
    return findings


def check_duplicates(notes: list[dict], config: dict) -> tuple[list[Finding], list[Advisory]]:
    """R005 -- cards that test the same thing, and same-stem families worth a look.

    A rule that simply compared prompts would fire on every legitimate list of
    similar facts ("What does a 400 response mean?" / "What does a 412 response
    mean?"), which is exactly how a code deck has to be written. So a violation
    needs the *answers* to collide as well:

    * identical prompt and identical answer -- the same card twice;
    * identical prompt, different answers -- one of the two is wrong, or the
      prompt is too vague to have one answer;
    * similar prompt and similar answer -- two near-identical cards.

    Similar prompt with clearly different answers is reported as an advisory
    instead: that is a same-stem family. It is legitimate, but families are where
    interference between similar cards shows up, so it is worth seeing.
    """
    threshold = config["duplicate_threshold"]
    findings: list[Finding] = []
    advisories: list[Advisory] = []

    prepared = []
    for note in notes:
        fields = note_fields(note)
        prepared.append((note, fields["prompt"], fields["answer"]))

    exact_seen: dict[tuple[str, str], str] = {}
    prompt_seen: dict[str, tuple[str, str]] = {}
    family_pairs: list[tuple[str, str]] = []

    for note, prompt, answer in prepared:
        norm_prompt, norm_answer = normalise(prompt), normalise(answer)
        if not norm_prompt:
            continue

        key = (norm_prompt, norm_answer)
        if key in exact_seen:
            findings.append(
                _f(note, "R005", f"same prompt and same answer as note {exact_seen[key]}")
            )
        else:
            exact_seen[key] = _note_id(note)

        if norm_prompt in prompt_seen:
            other_id, other_answer = prompt_seen[norm_prompt]
            if other_answer != norm_answer:
                findings.append(
                    _f(note, "R005", f"same prompt as note {other_id} but a different answer; "
                                     f"one of the two is wrong or the prompt has two answers")
                )
        else:
            prompt_seen[norm_prompt] = (_note_id(note), norm_answer)

    for i in range(len(prepared)):
        note_a, prompt_a, answer_a = prepared[i]
        if not normalise(prompt_a):
            continue
        for j in range(i + 1, len(prepared)):
            note_b, prompt_b, answer_b = prepared[j]
            if not normalise(prompt_b):
                continue
            if normalise(prompt_a) == normalise(prompt_b):
                continue  # handled above
            if similarity(prompt_a, prompt_b) < threshold:
                continue
            if similarity(answer_a, answer_b) >= threshold:
                findings.append(
                    _f(note_a, "R005", f"prompt and answer are both ~{threshold:.0%} similar to note {_note_id(note_b)}")
                )
                findings.append(
                    _f(note_b, "R005", f"prompt and answer are both ~{threshold:.0%} similar to note {_note_id(note_a)}")
                )
            else:
                family_pairs.append((_note_id(note_a), _note_id(note_b)))

    advisories.extend(_same_stem_advisories(family_pairs, prepared))
    return findings, advisories


def _same_stem_advisories(family_pairs: list, prepared: list) -> list["Advisory"]:
    """Group same-stem pairs into families so the report names each family once."""
    if not family_pairs:
        return []

    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for a, b in family_pairs:
        union(a, b)

    groups: dict[str, list[str]] = {}
    for item in list(parent):
        groups.setdefault(find(item), []).append(item)

    advisories = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members = sorted(members)
        advisories.append(
            Advisory(
                rule="R005",
                rule_name=RULES["R005"],
                note_ids=members,
                message=(
                    f"{len(members)} cards share a near-identical prompt but ask for different answers "
                    f"(same-stem family). This is normal for a list of codes; check that they do not "
                    f"interfere with each other when you review them."
                ),
            )
        )
    return sorted(advisories, key=lambda advisory: advisory.note_ids[0])


def check_topic(note: dict, config: dict) -> list[Finding]:
    """R006 -- every card belongs to a declared topic; that is what makes tags work."""
    if note.get("topic") is None or str(note.get("topic", "")).strip() == "":
        return [_f(note, "R006", "note has no topic, so it cannot be filed or tagged")]
    topic = str(note["topic"]).strip()
    if any(not segment.strip() for segment in topic.split("/")):
        return [_f(note, "R006", f"topic {topic!r} has an empty path segment")]
    return []


def check_source(note: dict, config: dict) -> list[Finding]:
    """R007 -- if a note claims a source, the source has to be there."""
    explicit = note.get("source_claimed")
    claimed = config["require_source"] if explicit is None else bool(explicit)
    source = str(note.get("source", "") or "").strip()
    if claimed and not source:
        return [
            _f(note, "R007", "note claims a source but none is given; set \"source_claimed\": false if it truly needs none")
        ]
    return []


def check_required_fields(note: dict, config: dict) -> list[Finding]:
    """R008 -- a card with a blank field cannot be answered."""
    kind = (note.get("type") or "basic").strip().lower()
    findings = []
    if kind == "cloze":
        if not str(note.get("text", "") or "").strip():
            findings.append(_f(note, "R008", "cloze note has no text"))
    elif kind == "basic":
        if not str(note.get("front", "") or "").strip():
            findings.append(_f(note, "R008", "note has no front (prompt)"))
        if not str(note.get("back", "") or "").strip():
            findings.append(_f(note, "R008", "note has no back (answer)"))
    return findings


def check_cloze(note: dict, config: dict) -> list[Finding]:
    """R009 -- cloze markers must be well formed and every deletion non-empty."""
    kind = (note.get("type") or "basic").strip().lower()
    if kind != "cloze":
        return []
    text = str(note.get("text", "") or "")
    findings = []
    numbers = [int(m.group(1)) for m in CLOZE_RE.finditer(text)]
    if not numbers:
        findings.append(_f(note, "R009", "cloze note has no {{c1::...}} marker"))
        return findings

    if any(not m.group(2).strip() for m in CLOZE_RE.finditer(text)):
        findings.append(_f(note, "R009", "cloze note has an empty deletion"))

    if len(set(numbers)) > config["max_cloze_deletions"]:
        findings.append(
            _f(note, "R009", f"{len(set(numbers))} deletions on one note; {config['max_cloze_deletions']} is the configured maximum")
        )

    # Nested or overlapping markers mean the note text is broken.
    if text.count("{{c") != len(numbers):
        findings.append(_f(note, "R009", "unbalanced or nested cloze markers"))
    if re.search(r"\{\{c\d+::[^{}]*\{\{", text):
        findings.append(_f(note, "R009", "cloze marker opens inside another marker"))

    # A deletion that swallows the whole sentence leaves no context to answer from.
    stripped = CLOZE_RE.sub("", text).strip()
    if len(stripped) < config["min_prompt_chars"]:
        findings.append(_f(note, "R009", "cloze deletions leave too little context for the card to make sense"))

    # The rearward hint syntax {{c1::answer::hint}} must not be empty.
    for match in CLOZE_RE.finditer(text):
        if match.group(3) is not None and not match.group(3).strip():
            findings.append(_f(note, "R009", "cloze hint is empty"))
            break
    return findings


def check_ids(notes: list[dict], config: dict) -> list[Finding]:
    """R010 -- ids are what keep scheduling history across regeneration."""
    findings = []
    seen: dict[str, int] = {}
    for index, note in enumerate(notes):
        note_id = note.get("id")
        if note_id is None or str(note_id).strip() == "":
            findings.append(_f(note, "R010", f"note at position {index + 1} has no id"))
            continue
        note_id = str(note_id).strip()
        if note_id in seen:
            findings.append(
                _f(note, "R010", f"id {note_id!r} is already used by the note at position {seen[note_id] + 1}")
            )
        else:
            seen[note_id] = index
    return findings


def check_tags(note: dict, config: dict) -> list[Finding]:
    """R011 -- Anki tags cannot contain spaces."""
    tags = note.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    findings = []
    for tag in tags:
        tag = str(tag)
        if any(char in tag for char in config["forbidden_tag_chars"]):
            findings.append(_f(note, "R011", f"tag {tag!r} contains a space or newline"))
        elif not tag.strip():
            findings.append(_f(note, "R011", "empty tag"))
    return findings


def check_prompt_leak(note: dict, config: dict) -> list[Finding]:
    """R012 -- if the prompt spells out the answer, the card tests nothing."""
    kind = (note.get("type") or "basic").strip().lower()
    if kind == "cloze":
        return []
    fields = note_fields(note)
    prompt, answer = fields["prompt"], fields["answer"]
    if not prompt.strip() or not answer.strip():
        return []

    answer_words = content_words(answer)
    if len(answer_words) < 3:
        return []
    prompt_words = content_words(prompt)
    overlap = answer_words & prompt_words
    ratio = len(overlap) / len(answer_words)

    if ratio >= config["leak_threshold"]:
        return [
            _f(note, "R012", f"{ratio:.0%} of the answer's key words already appear in the prompt ({', '.join(sorted(overlap))})")
        ]
    normalised_answer = normalise(answer)
    if len(normalised_answer.split()) >= 2 and normalised_answer in normalise(prompt):
        return [_f(note, "R012", "the answer appears verbatim in the prompt")]
    return []


def _note_id(note: dict) -> str:
    note_id = note.get("id")
    if note_id is None or str(note_id).strip() == "":
        return "<no-id>"
    return str(note_id)


def _f(note: dict, rule: str, message: str) -> Finding:
    return Finding(note_id=_note_id(note), rule=rule, rule_name=RULES[rule], message=message)


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def audit_document(document: dict, config: dict | None = None) -> tuple[list[Finding], list[Advisory]]:
    """Lint a whole notes document. Returns (violations, advisories).

    Violations are rule breaches: they fail the build. Advisories are structural
    observations that do not breach a rule but are worth seeing, such as a large
    same-stem family.
    """
    merged = dict(DEFAULT_CONFIG)
    if config:
        merged.update(config)

    notes = document.get("notes", [])
    declared_topics = set()
    for topic in document.get("topics", []) or []:
        if isinstance(topic, dict) and topic.get("path"):
            declared_topics.add(str(topic["path"]).strip())
        elif isinstance(topic, str):
            declared_topics.add(topic.strip())

    findings: list[Finding] = []
    # Ids are checked across the whole document: a duplicate is only visible when
    # every note is compared with every other one.
    findings.extend(check_ids(notes, merged))
    for note in notes:
        if not isinstance(note, dict):
            continue
        findings.extend(check_required_fields(note, merged))
        findings.extend(check_topic(note, merged))
        if declared_topics and str(note.get("topic", "")).strip() not in declared_topics:
            findings.append(
                _f(note, "R006", f"topic {str(note.get('topic','')).strip()!r} is not declared in the deck's \"topics\" list")
            )
        findings.extend(check_source(note, merged))
        findings.extend(check_one_fact(note, merged))
        findings.extend(check_unambiguous_prompt(note, merged))
        findings.extend(check_negative_wording(note, merged))
        findings.extend(check_answer_length(note, merged))
        findings.extend(check_cloze(note, merged))
        findings.extend(check_tags(note, merged))
        findings.extend(check_prompt_leak(note, merged))

    duplicate_findings, advisories = check_duplicates(notes, merged)
    findings.extend(duplicate_findings)

    def sort_key(finding: Finding):
        return (finding.note_id, finding.rule)

    return sorted(findings, key=sort_key), advisories


def validate_document(document: dict, config: dict | None = None) -> list[Finding]:
    """Lint a whole notes document, returning only the rule violations."""
    findings, _ = audit_document(document, config)
    return findings


def summarise(findings: list[Finding]) -> dict:
    by_rule: dict[str, int] = {}
    for finding in findings:
        by_rule[finding.rule] = by_rule.get(finding.rule, 0) + 1
    return {"total": len(findings), "by_rule": dict(sorted(by_rule.items()))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_notes.py",
        description="Lint spaced-repetition source notes before they become an Anki deck.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit status: 0 clean, 1 violations, 2 unreadable input.",
    )
    parser.add_argument("path", nargs="?", help="notes file (JSON or YAML)")
    parser.add_argument("--max-answer-chars", type=int, default=DEFAULT_CONFIG["max_answer_chars"],
                        help="R004 limit for answer length (default: %(default)s)")
    parser.add_argument("--min-prompt-chars", type=int, default=DEFAULT_CONFIG["min_prompt_chars"],
                        help="R002 minimum prompt length (default: %(default)s)")
    parser.add_argument("--duplicate-threshold", type=float, default=DEFAULT_CONFIG["duplicate_threshold"],
                        help="R005 similarity ratio that counts as a near duplicate (default: %(default)s)")
    parser.add_argument("--max-cloze-deletions", type=int, default=DEFAULT_CONFIG["max_cloze_deletions"],
                        help="R009 maximum deletions per cloze note (default: %(default)s)")
    parser.add_argument("--leak-threshold", type=float, default=DEFAULT_CONFIG["leak_threshold"],
                        help="R012 fraction of answer words allowed in the prompt (default: %(default)s)")
    parser.add_argument("--no-require-source", dest="require_source", action="store_false",
                        help="R007: do not demand a source from notes that do not claim one")
    parser.add_argument("--warnings-as-errors", action="store_true",
                        help="treat advisories as failures too (exit 1 when any are reported)")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    parser.add_argument("--list-rules", action="store_true", help="print the rule catalogue and exit")
    parser.add_argument("--quiet", action="store_true", help="print only the summary line")
    args = parser.parse_args(argv)

    if args.list_rules:
        for rule, name in RULES.items():
            print(f"{rule}  {name}")
            print(f"      {RULE_HELP[rule]}")
        return 0

    if not args.path:
        parser.error("a notes file is required (or use --list-rules)")

    config = {
        "max_answer_chars": args.max_answer_chars,
        "min_prompt_chars": args.min_prompt_chars,
        "duplicate_threshold": args.duplicate_threshold,
        "max_cloze_deletions": args.max_cloze_deletions,
        "leak_threshold": args.leak_threshold,
        "require_source": args.require_source,
    }

    try:
        document = load_document(args.path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    findings, advisories = audit_document(document, config)
    counts = summarise(findings)
    note_count = len(document.get("notes", []))

    if args.json:
        print(json.dumps({"file": args.path, "notes": note_count,
                          "findings": [f.as_dict() for f in findings],
                          "advisories": [a.as_dict() for a in advisories],
                          **counts}, indent=2))
    else:
        if not args.quiet:
            for finding in findings:
                print(finding.format_line())
            for advisory in advisories:
                print(advisory.format_line())
        status = "FAIL" if findings else "PASS"
        print(f"{status} {args.path}: {note_count} notes, {counts['total']} violation(s), "
              f"{len(advisories)} advisory(ies)")
        if findings:
            print("by rule: " + ", ".join(f"{rule}={n}" for rule, n in counts["by_rule"].items()))
            print("hint: run with --list-rules for the full rule catalogue")

    if findings:
        return 1
    if advisories and args.warnings_as_errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
