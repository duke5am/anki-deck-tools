#!/usr/bin/env python3
"""build_deck.py -- turn structured notes into a well-formed Anki .apkg deck.

WHAT IT DOES
------------
Reads a source file (JSON or YAML) that describes notes organised by topic, and
writes a real Anki package that imports cleanly into Anki 2.1.

Design decisions that matter to you as a buyer:

* **A real note type, not a text dump.** Each deck gets its own note type with
  declared fields (Front, Back, Source, Topic) and card templates, plus a cloze
  note type for fill-in-the-blank material. The templates render the source
  attribution and the topic on the answer side, so provenance travels with every
  card instead of living in a README nobody opens.

* **Stable GUIDs, so regenerating does not duplicate your cards.** Anki matches
  imported notes by GUID. This generator derives the GUID from the note's
  ``id`` in the source file, not from its text. Editing a card's wording
  therefore keeps the same GUID, and Anki updates the existing note in place --
  your review history, intervals and due dates survive. See docs/REGENERATING.md
  for the full explanation and the pitfalls (change an id and you have created a
  new card; leave one out and the file will not build).

* **Tags derived from the topic hierarchy.** A note in topic
  ``Git/Objects and History/Commits`` gets ``git``, ``git::objects-and-history``
  and ``git::objects-and-history::commits``, so you can study or suspend a slice
  of a deck without unsubscribing from the whole thing.

* **Ids that cannot collide across decks.** Note and card ids are allocated from
  a per-deck range, so importing several decks into one Anki profile never makes
  two decks fight over the same id.

* **A deck description.** Anki shows it in the deck overview, so the deck says
  what it is, how many cards it has and where the material came from.

QUALITY GATE
------------
By default the build runs ``validate_notes.py`` first and refuses to write a
package that violates the card-writing rules. ``--skip-lint`` exists, but a deck
built with it is a deck you have not checked.

USAGE
-----
    python3 build_deck.py --input notes.json --out deck.apkg
    python3 build_deck.py --input notes.yaml --out deck.apkg \\
        --deck-name "HTTP Status Codes" --deterministic --manifest deck.manifest.json

Exit status: 0 success, 1 build/verification error, 2 input or lint failure.

DEPENDENCIES
------------
Python 3.9+ and genanki (tested with genanki 0.13.1). PyYAML is needed only for
.yaml input.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import itertools
import json
import os
import re
import sqlite3
import sys
import time
import warnings
import zipfile
from dataclasses import dataclass, field as dataclass_field

GENERATOR_NAME = "anki-deck-generator"
GENERATOR_VERSION = "1.0.0"

#: Anki note/card id ranges per deck. The anchor for a deck's id block is derived
#: from its key, so ids are reproducible and two decks never share an id block.
ID_RANGE = (10 ** 12, 2 * 10 ** 12)
#: Note type and deck ids live in separate namespaces so they cannot collide.
DECK_ID_RANGE = (1_600_000_000, 1_699_999_999)
MODEL_ID_RANGE = (1_700_000_000, 1_799_999_999)
#: Fixed timestamp epoch for a deterministic build: plausible-looking, and far
#: enough in the past that a real edit always wins a conflict.
DETERMINISTIC_TIME_RANGE = (1_000_000_000, 2_000_000_000)
#: Zip members carry this modification time in deterministic mode (the earliest
#: value the zip format can represent), so no wall-clock value enters the file.
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

MODEL_CSS = """\
/* Styling for the decks built by anki-deck-generator.
   Plain, high contrast, no animation: this is reading material. */
.card {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 20px;
  line-height: 1.45;
  text-align: left;
  color: #16181d;
  background-color: #fbfbf7;
  padding: 14px 16px;
}
.prompt { font-size: 21px; }
#answer { border: 0; border-top: 1px solid #d5d5cc; margin: 14px 0; }
.answer { font-size: 22px; font-weight: 600; }
.extra { font-size: 18px; color: #33383f; margin-top: 10px; }
.source, .topic { font-size: 13px; color: #6b7280; margin-top: 8px; }
.topic { font-style: italic; }
.cloze { font-weight: 600; color: #1d4ed8; }
"""

#: Field layout per note type. The first field is the sort field.
BASIC_FIELDS = ["Front", "Back", "Source", "Topic"]
CLOZE_FIELDS = ["Text", "Extra", "Source", "Topic"]

_SOURCE_AND_TOPIC = (
    '{{#Source}}<div class="source">Source: {{Source}}</div>{{/Source}}'
    '{{#Topic}}<div class="topic">{{Topic}}</div>{{/Topic}}'
)


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------


def stable_int(*parts, lo: int, hi: int) -> int:
    """A reproducible integer in [lo, hi] derived from ``parts``.

    Used for deck ids, note type ids and id-block anchors, so that regenerating a
    deck produces the same ids and Anki treats it as the same deck rather than a
    second copy.
    """
    if hi < lo:
        raise ValueError("hi must be >= lo")
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).digest()
    return lo + (int.from_bytes(digest[:8], "big") % (hi - lo + 1))


def slug_tag(text: str) -> str:
    """Make ``text`` usable as an Anki tag: lowercase, no spaces, no stray syntax."""
    text = str(text).strip().lower()
    text = text.replace("/", "-")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^0-9a-z\-_.]+", "", text)
    return text.strip("-._:")


def topic_tags(topic: str) -> list[str]:
    """Turn ``A/B/C`` into the cumulative tag list ``a``, ``a::b``, ``a::b::c``."""
    segments = [slug_tag(part) for part in str(topic).split("/")]
    segments = [segment for segment in segments if segment]
    return ["::".join(segments[: index + 1]) for index in range(len(segments))]


def slug_key(text: str) -> str:
    return re.sub(r"-+", "-", slug_tag(text)) or "deck"


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Note types
# --------------------------------------------------------------------------


def cloze_required_fields_override(model) -> None:
    """Give the cloze note type the ``req`` value Anki's own cloze type uses.

    genanki computes ``req`` by rendering the question template with each field
    blanked in turn. A cloze template (``{{cloze:Text}}``) is opaque to that
    renderer, so genanki marks *every* field as required, which is not what Anki
    stores for its built-in cloze note type. Card generation for cloze notes does
    not consult ``req`` (a card is produced per ``{{cN::...}}`` marker), so this
    is a metadata correction, not a workaround -- but it keeps the note type
    honest in Anki's editor. If a future genanki changes the attribute, the
    genanki-computed value is used instead and decks still build.
    """
    try:
        model._req = [[0, "any", [0]]]
    except Exception:  # pragma: no cover - defensive, genanki internals changed
        pass


def make_models(deck_key: str, deck_title: str) -> dict:
    """Build the note types for a deck: recall, reverse and cloze."""
    import genanki

    base = stable_int("model", deck_key, lo=MODEL_ID_RANGE[0], hi=MODEL_ID_RANGE[1] - 2)
    models = {}

    recall = genanki.Model(
        base,
        f"{deck_title} - Recall",
        fields=[{"name": name} for name in BASIC_FIELDS],
        templates=[
            {
                "name": "Prompt to answer",
                "qfmt": '<div class="prompt">{{Front}}</div>',
                "afmt": (
                    '{{FrontSide}}<hr id="answer">'
                    '<div class="answer">{{Back}}</div>'
                    + _SOURCE_AND_TOPIC
                ),
            }
        ],
        css=MODEL_CSS,
    )
    models["recall"] = recall

    reverse = genanki.Model(
        base + 1,
        f"{deck_title} - Reverse",
        fields=[{"name": name} for name in BASIC_FIELDS],
        templates=[
            {
                "name": "Answer to prompt",
                "qfmt": '<div class="prompt">{{Back}}</div>',
                "afmt": (
                    '{{FrontSide}}<hr id="answer">'
                    '<div class="answer">{{Front}}</div>'
                    + _SOURCE_AND_TOPIC
                ),
            }
        ],
        css=MODEL_CSS,
    )
    models["reverse"] = reverse

    cloze = genanki.Model(
        base + 2,
        f"{deck_title} - Cloze",
        fields=[{"name": name} for name in CLOZE_FIELDS],
        templates=[
            {
                "name": "Cloze",
                "qfmt": '<div class="prompt">{{cloze:Text}}</div>' + _SOURCE_AND_TOPIC,
                "afmt": (
                    '{{cloze:Text}}'
                    '{{#Extra}}<hr id="answer"><div class="extra">{{Extra}}</div>{{/Extra}}'
                    + _SOURCE_AND_TOPIC
                ),
            }
        ],
        css=MODEL_CSS,
        model_type=genanki.Model.CLOZE,
    )
    cloze_required_fields_override(cloze)
    models["cloze"] = cloze

    return models


# --------------------------------------------------------------------------
# GUIDs
# --------------------------------------------------------------------------


def note_guid(deck_key: str, note_id: str, model_key: str, strategy: str, fields: list[str]) -> str:
    """The identity Anki uses to decide whether a note is new or an update.

    ``id`` (default)
        ``GUID = hash(deck_key, note_id, model_key)`` -- a pure function of the
        note's declared id. Fix a typo, reword a card, correct a source and the
        GUID is unchanged, so Anki updates the card you already have and keeps
        its scheduling history.
    ``content``
        genanki's default: the GUID is a hash of the field text. Two notes with
        identical text are treated as one card, but editing any word creates a
        new card and the old scheduling history is stranded. Useful only when
        the source is machine-generated and never hand-edited.
    """
    import genanki

    if strategy == "content":
        return genanki.guid_for(*fields)
    if strategy != "id":
        raise ValueError(f"unknown guid strategy {strategy!r}")
    return genanki.guid_for(deck_key, note_id, model_key)


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------


@dataclass
class BuildResult:
    out_path: str
    deck_key: str
    deck_id: int
    deck_title: str
    note_count: int
    card_count: int
    guids: list
    model_ids: dict
    size_bytes: int
    sha256: str
    members: list
    timestamp: float
    deterministic: bool
    warnings: list = dataclass_field(default_factory=list)

    def manifest(self) -> dict:
        return {
            "generator": f"{GENERATOR_NAME} {GENERATOR_VERSION}",
            "deck_key": self.deck_key,
            "deck_id": self.deck_id,
            "deck_title": self.deck_title,
            "note_count": self.note_count,
            "card_count": self.card_count,
            "model_ids": self.model_ids,
            "guids": list(self.guids),
            "guid_fingerprint": hashlib.sha256("\n".join(self.guids).encode("utf-8")).hexdigest(),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "members": self.members,
            "timestamp": self.timestamp,
            "deterministic": self.deterministic,
        }


def _deck_config(document: dict) -> dict:
    deck = document.get("deck") or {}
    if not isinstance(deck, dict):
        raise ValueError('"deck" must be an object')
    return deck


def _resolve_timestamp(deck_key: str, deterministic: bool, timestamp) -> float:
    if timestamp is not None:
        return float(timestamp)
    if deterministic:
        return float(stable_int("time", deck_key, lo=DETERMINISTIC_TIME_RANGE[0], hi=DETERMINISTIC_TIME_RANGE[1]))
    return float(time.time())


_USED_ID_BASES: set = set()


def _resolve_id_base(deck_key: str, deterministic: bool, block_size: int) -> int:
    """First id in this deck's id block.

    Deterministic builds derive the block from the deck key: reproducible, and
    different decks get different blocks, so several decks can live in one Anki
    collection without id clashes.
    """
    if deterministic:
        return stable_int("noteid", deck_key, lo=ID_RANGE[0], hi=ID_RANGE[1] - block_size - 1)
    base = int(time.time() * 1000)
    while any(abs(base - used) < block_size + 1 for used in _USED_ID_BASES):
        base += block_size + 1
    _USED_ID_BASES.add(base)
    return base


def deck_description(document: dict, deck_title: str, note_count: int, card_count: int, source_path: str) -> str:
    deck = _deck_config(document)
    parts = []
    if deck.get("description"):
        parts.append(str(deck["description"]).strip())
    parts.append(
        f"{note_count} notes / {card_count} cards. Built from source notes for this deck with "
        f"{GENERATOR_NAME} {GENERATOR_VERSION}; regenerate it from those notes at any time."
    )
    parts.append(
        "Every card shows its source. This is original study material, not official exam material, "
        "and it is not affiliated with or endorsed by any certification body. Verify anything that "
        "matters against the source named on the card."
    )
    if source_path:
        parts.append(f"Source notes: {os.path.basename(source_path)}")
    return "\n\n".join(parts)


def build(document: dict, out_path: str, deck_name: str | None = None, deterministic: bool = False,
          guid_strategy: str = "id", timestamp=None, source_path: str = "") -> BuildResult:
    """Build one .apkg from a notes document."""
    import genanki

    if not isinstance(document, dict) or "notes" not in document:
        raise ValueError('document must be an object with a "notes" list')
    notes = document["notes"]
    if not notes:
        raise ValueError("document contains no notes")

    deck = _deck_config(document)
    deck_key = str(deck.get("key") or slug_key(deck.get("name") or os.path.splitext(os.path.basename(out_path))[0]))
    deck_title = str(deck_name or deck.get("name") or deck_key)
    deck_id = int(deck.get("deck_id") or stable_int("deck", deck_key, lo=DECK_ID_RANGE[0], hi=DECK_ID_RANGE[1]))

    models = make_models(deck_key, deck_title)
    resolved_timestamp = _resolve_timestamp(deck_key, deterministic, timestamp)

    # Which notes exist, and which note type each one needs.
    prepared = []
    for note in notes:
        note_id = str(note.get("id", "")).strip()
        kind = str(note.get("type") or "basic").strip().lower()
        reverse = bool(note.get("reverse")) and kind == "basic"
        if kind == "cloze":
            model_key = "cloze"
        elif reverse:
            model_key = "reverse"
        else:
            model_key = "recall"

        if model_key == "cloze":
            fields = [
                str(note.get("text", "") or ""),
                str(note.get("extra", "") or ""),
                str(note.get("source", "") or ""),
                str(note.get("topic", "") or ""),
            ]
        else:
            fields = [
                str(note.get("front", "") or ""),
                str(note.get("back", "") or ""),
                str(note.get("source", "") or ""),
                str(note.get("topic", "") or ""),
            ]

        tags: list[str] = []
        for tag in topic_tags(note.get("topic", "")):
            if tag not in tags:
                tags.append(tag)
        for tag in note.get("tags") or []:
            cleaned = slug_tag(tag)
            if cleaned and cleaned not in tags:
                tags.append(cleaned)
        if deck_key not in tags:
            tags.append(deck_key)

        prepared.append((note_id, model_key, fields, tags, note))

    # Ids: every note and every card it spawns takes the next id in the block.
    ordered = sorted(prepared, key=lambda item: item[0])
    block_size = 4 * len(ordered) + 8
    id_base = _resolve_id_base(deck_key, deterministic, block_size)

    deck_object = genanki.Deck(deck_id, deck_title)
    guids: list[str] = []

    for note_id, model_key, fields, tags, note in ordered:
        guid = note_guid(deck_key, note_id, model_key, guid_strategy, fields)
        guids.append(guid)
        # Fields are plain text in the source notes; escape the three characters
        # that would otherwise be read as HTML markup, so a card may contain
        # "<", ">" or "&" without the deck being mis-rendered. quote=False keeps
        # apostrophes and quotation marks as typed.
        genanki_note = genanki.Note(
            model=models[model_key],
            fields=[html.escape(value, quote=False) for value in fields],
            tags=tags,
            guid=guid,
        )
        deck_object.add_note(genanki_note)

    card_count = sum(len(genanki_note.cards) for genanki_note in deck_object.notes)

    deck_object.description = deck_description(document, deck_title, len(ordered), card_count, source_path)

    package = genanki.Package(deck_object)
    warnings_captured: list[str] = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        members = _write_package(package, out_path, id_base, resolved_timestamp, deterministic)
        warnings_captured = [str(item.message) for item in caught]

    result = BuildResult(
        out_path=out_path,
        deck_key=deck_key,
        deck_id=deck_id,
        deck_title=deck_title,
        note_count=len(ordered),
        card_count=card_count,
        guids=guids,
        model_ids={key: model.model_id for key, model in models.items()},
        size_bytes=os.path.getsize(out_path),
        sha256=sha256_file(out_path),
        members=members,
        timestamp=resolved_timestamp,
        deterministic=deterministic,
        warnings=warnings_captured,
    )
    return result


def _write_package(package, out_path: str, id_base: int, timestamp: float, deterministic: bool) -> list:
    """Write the .apkg.

    genanki's own writer seeds note ids from the wall clock and lets the zip
    container record the current time, so the same input never produces the same
    file twice. Here the collection is written to a scratch file with an id
    generator we control, and the container is assembled explicitly: in
    deterministic mode every member gets a fixed modification time and a fixed
    attribute word, and the compression level is pinned. The scratch directory
    sits next to the output rather than in the system temp dir, so the build does
    not depend on /tmp being writable.
    """
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    scratch_dir = os.path.join(out_dir, ".build-scratch")
    os.makedirs(scratch_dir, exist_ok=True)
    dbfile = os.path.join(scratch_dir, "collection-{}.sqlite".format(os.getpid()))
    if os.path.exists(dbfile):
        os.remove(dbfile)

    try:
        connection = sqlite3.connect(dbfile)
        try:
            cursor = connection.cursor()
            package.write_to_db(cursor, timestamp, itertools.count(id_base))
            connection.commit()
        finally:
            connection.close()

        with open(dbfile, "rb") as handle:
            collection_bytes = handle.read()
    finally:
        if os.path.exists(dbfile):
            os.remove(dbfile)
        _remove_if_empty(scratch_dir)

    members = [("collection.anki2", collection_bytes), ("media", json.dumps({}))]

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(members, key=lambda item: item[0]):
            if deterministic:
                info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0
                info.external_attr = 0o644 << 16
                archive.writestr(info, data)
            else:
                archive.writestr(name, data)

    return [name for name, _ in sorted(members, key=lambda item: item[0])]


def _remove_if_empty(path: str) -> None:
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def read_collection(apkg_path: str) -> sqlite3.Connection:
    """Open the SQLite collection stored inside an .apkg, in memory.

    Reading the collection out of the package is the only honest way to prove a
    deck is real: it shows the notes and cards Anki would actually import.
    """
    with zipfile.ZipFile(apkg_path) as archive:
        names = archive.namelist()
        member = None
        for candidate in ("collection.anki21", "collection.anki2"):
            if candidate in names:
                member = candidate
                break
        if member is None:
            raise ValueError(f"{apkg_path}: no collection.anki2 member found (members: {names})")
        data = archive.read(member)
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(data)
    except AttributeError:  # Python < 3.11 has no deserialize; use a scratch file
        connection.close()
        out_dir = os.path.dirname(os.path.abspath(apkg_path)) or "."
        scratch_dir = os.path.join(out_dir, ".build-scratch")
        os.makedirs(scratch_dir, exist_ok=True)
        temp_path = os.path.join(scratch_dir, "verify-{}.sqlite".format(os.getpid()))
        with open(temp_path, "wb") as handle:
            handle.write(data)
        connection = sqlite3.connect(temp_path)
    return connection


def verify_apkg(apkg_path: str) -> dict:
    """Everything a test wants to assert about a produced deck."""
    result: dict = {"path": apkg_path, "size_bytes": os.path.getsize(apkg_path), "sha256": sha256_file(apkg_path)}
    with zipfile.ZipFile(apkg_path) as archive:
        result["members"] = sorted(archive.namelist())
        if archive.testzip() is not None:
            raise ValueError(f"{apkg_path}: zip is corrupt")

    connection = read_collection(apkg_path)
    try:
        cursor = connection.cursor()
        result["note_count"] = cursor.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        result["card_count"] = cursor.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        result["guids"] = [row[0] for row in cursor.execute("SELECT guid FROM notes ORDER BY id").fetchall()]
        result["note_ids"] = [row[0] for row in cursor.execute("SELECT id FROM notes ORDER BY id").fetchall()]
        result["models"] = json.loads(cursor.execute("SELECT models FROM col").fetchone()[0])
        result["decks"] = json.loads(cursor.execute("SELECT decks FROM col").fetchone()[0])
        result["schema_version"] = cursor.execute("SELECT ver FROM col").fetchone()[0]
    finally:
        connection.close()

    result["model_fields"] = {
        model["name"]: [field["name"] for field in model["flds"]] for model in result["models"].values()
    }
    result["model_names"] = sorted(model["name"] for model in result["models"].values())
    result["deck_names"] = sorted(deck["name"] for deck in result["decks"].values())
    result["deck_descriptions"] = {
        deck["name"]: deck.get("desc", "") for deck in result["decks"].values()
    }
    result["tags"] = sorted(
        {tag for row in _read_tags(apkg_path) for tag in row}
    )
    return result


def _read_tags(apkg_path: str) -> list:
    connection = read_collection(apkg_path)
    try:
        rows = connection.execute("SELECT tags FROM notes").fetchall()
    finally:
        connection.close()
    return [[tag for tag in row[0].split() if tag] for row in rows]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def load_document(path: str) -> dict:
    """Load a notes document. Imported lazily so the module stays dependency-light."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from validate_notes import load_document as _load

    return _load(path)


def format_report(result: BuildResult) -> str:
    mode = "deterministic" if result.deterministic else "timestamped"
    lines = [
        f"built {result.out_path}",
        f"  deck        {result.deck_title} (id {result.deck_id}, key {result.deck_key})",
        f"  notes       {result.note_count}",
        f"  cards       {result.card_count}",
        f"  note types  " + ", ".join(f"{name}={mid}" for name, mid in sorted(result.model_ids.items())),
        f"  guids       {len(set(result.guids))} distinct of {len(result.guids)}",
        f"  size        {result.size_bytes} bytes",
        f"  sha256      {result.sha256}",
        f"  mode        {mode} (pinned timestamp {int(result.timestamp)})",
    ]
    if result.warnings:
        lines.append(f"  warnings    {len(result.warnings)}")
        for warning in result.warnings:
            lines.append(f"    - {warning}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_deck.py",
        description="Build an Anki .apkg deck from structured JSON or YAML notes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit status: 0 success, 1 build error, 2 input or lint failure.",
    )
    parser.add_argument("source", nargs="?", help="notes file (JSON or YAML)")
    parser.add_argument("--input", dest="input_path", help="notes file (same as the positional argument)")
    parser.add_argument("--out", required=True, help="path of the .apkg to write")
    parser.add_argument("--deck-name", help="override the deck name from the source file")
    parser.add_argument("--deterministic", action="store_true",
                        help="pin timestamps and normalize the zip container so the same input gives the "
                             "same bytes (GUIDs are stable with or without this flag)")
    parser.add_argument("--guid-strategy", choices=["id", "content"], default="id",
                        help="id (default): GUID follows the note id, so edits keep scheduling history; "
                             "content: GUID follows the card text, so any edit makes a new card")
    parser.add_argument("--timestamp", type=float, default=None,
                        help="explicit timestamp for note/card modification times")
    parser.add_argument("--manifest", help="write a JSON build manifest (ids, GUIDs, hashes) to this path")
    parser.add_argument("--skip-lint", action="store_true",
                        help="build even if the notes fail the card-writing rules (not recommended)")
    parser.add_argument("--max-answer-chars", type=int, default=None, help="lint limit for answer length")
    parser.add_argument("--verify", action="store_true", help="after building, re-open the .apkg and report its contents")
    parser.add_argument("--quiet", action="store_true", help="print only the output path")
    args = parser.parse_args(argv)

    source = args.input_path or args.source
    if not source:
        parser.error("a notes file is required (positional or --input)")
    if not os.path.exists(source):
        print(f"error: no such notes file: {source}", file=sys.stderr)
        return 2

    try:
        document = load_document(source)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.skip_lint:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from validate_notes import summarise, validate_document

        lint_config = {}
        if args.max_answer_chars is not None:
            lint_config["max_answer_chars"] = args.max_answer_chars
        findings = validate_document(document, lint_config or None)
        if findings:
            print(f"refusing to build {source}: {len(findings)} card-writing violation(s)", file=sys.stderr)
            for finding in findings:
                print("  " + finding.format_line(), file=sys.stderr)
            print("by rule: " + ", ".join(f"{rule}={n}" for rule, n in summarise(findings)["by_rule"].items()),
                  file=sys.stderr)
            print("fix the notes, or pass --skip-lint to build anyway", file=sys.stderr)
            return 2

    try:
        result = build(
            document,
            args.out,
            deck_name=args.deck_name,
            deterministic=args.deterministic,
            guid_strategy=args.guid_strategy,
            timestamp=args.timestamp,
            source_path=source,
        )
    except (OSError, ValueError, TypeError) as exc:
        print(f"error: build failed: {exc}", file=sys.stderr)
        return 1

    if args.manifest:
        manifest_dir = os.path.dirname(os.path.abspath(args.manifest))
        os.makedirs(manifest_dir, exist_ok=True)
        with open(args.manifest, "w", encoding="utf-8") as handle:
            json.dump(result.manifest(), handle, indent=2, sort_keys=True)
            handle.write("\n")

    if args.quiet:
        print(result.out_path)
    else:
        print(format_report(result))

    if args.verify:
        info = verify_apkg(result.out_path)
        print("verify:")
        print(f"  members     {', '.join(info['members'])}")
        print(f"  notes       {info['note_count']}")
        print(f"  cards       {info['card_count']}")
        print(f"  note types  {', '.join(info['model_names'])}")
        print(f"  decks       {', '.join(info['deck_names'])}")
        print(f"  schema      {info['schema_version']}")
        mismatch = [problem for problem in _verify_consistency(result, info)]
        if mismatch:
            for problem in mismatch:
                print(f"  MISMATCH    {problem}", file=sys.stderr)
            return 1
        print("  consistent  counts, guids and note types match the build report")

    return 0


def _verify_consistency(result: BuildResult, info: dict) -> list:
    problems = []
    if info["note_count"] != result.note_count:
        problems.append(f"note count {info['note_count']} != built {result.note_count}")
    if info["card_count"] != result.card_count:
        problems.append(f"card count {info['card_count']} != built {result.card_count}")
    if sorted(info["guids"]) != sorted(result.guids):
        problems.append("guids in the package differ from the guids that were generated")
    if result.deck_title not in info["deck_names"]:
        problems.append(f"deck {result.deck_title!r} missing from the package")
    for name in info["model_names"]:
        if not name.startswith(result.deck_title):
            problems.append(f"unexpected note type {name!r}")
    return problems


if __name__ == "__main__":
    sys.exit(main())
