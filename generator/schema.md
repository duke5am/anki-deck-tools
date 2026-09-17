# The note format

`build_deck.py` reads one file that describes a deck and its notes. The file can
be JSON or YAML; JSON is what ships with the four decks in `decks/`.

You do not have to write this by hand. The easiest path is to copy one of the
shipped `.notes.json` files, delete the notes you do not want, and edit what is
left. The linter will tell you when a note breaks a card-writing rule.

---

## 1. Top level

```json
{
  "schema_version": 1,
  "deck": {
    "key": "http-status-codes",
    "name": "HTTP Status Codes and Semantics",
    "description": "One or two sentences for the deck overview in Anki."
  },
  "topics": [
    { "path": "HTTP/Status Code Classes", "description": "What the first digit tells a client." }
  ],
  "notes": [
    { "id": "http-001", "topic": "HTTP/Status Code Classes", "front": "...", "back": "...", "source": "RFC 9110" }
  ]
}
```

| Field | Required | What it does |
|---|---|---|
| `schema_version` | no | Informational, currently `1`. |
| `deck.key` | **yes** | A short stable slug. Deck ids, note type ids and **every GUID** are derived from it. See `docs/REGENERATING.md` before changing it. |
| `deck.name` | **yes** | The deck name in Anki. `--deck-name` overrides it at build time. |
| `deck.description` | recommended | Shown in Anki's deck overview, above the generated provenance text. |
| `deck.deck_id` | no | Overrides the derived Anki deck id. Leave it out. |
| `topics` | recommended | The declared topic list. A note whose topic is not declared here fails the linter. |
| `notes` | **yes** | The list. Note order does not matter; output is sorted by id. |

`topics[].description` is documentation for you. It is not stored in the deck.

---

## 2. A note

Every note needs `id`, `topic` and `source` (or an explicit opt-out, see below).

| Field | Required | Meaning |
|---|---|---|
| `id` | **yes** | Stable, unique within the file. This is the note's identity across regenerations. Use `prefix-001`, `prefix-002`, … and never renumber. |
| `topic` | **yes** | A slash-separated path that must appear in `topics`. |
| `type` | no | `basic` (default) or `cloze`. |
| `source` | yes unless opted out | Where the fact comes from. Shown on the answer side of every card. |
| `source_claimed` | no | Set to `false` to declare that a note genuinely needs no source. If it is `true` or absent, an empty `source` is a lint violation. |
| `tags` | no | Extra Anki tags. No spaces allowed; use hyphens. Topic tags are generated for you. |
| `reverse` | no | `true` on a `basic` note makes the back the prompt and the front the answer. Use only when both directions have exactly one answer. |
| `allow_negative` | no | Opts a single note out of rule **R003**. Only for prompts where the negation *is* the fact, such as a question about the `EXCEPT` operator. Never use it to silence a badly worded card. |

### Basic notes (question and answer)

```json
{
  "id": "sql-045",
  "topic": "SQL/NULL and Three-Valued Logic",
  "front": "Which rows are included by COUNT(*) but skipped by COUNT(column)?",
  "back": "Rows where that column holds NULL.",
  "source": "SQL:2016 (ISO/IEC 9075)"
}
```

### Cloze notes (fill in the blank)

```json
{
  "id": "sql-057",
  "type": "cloze",
  "topic": "SQL/NULL and Three-Valued Logic",
  "text": "COUNT(*) counts {{c1::rows}}, while COUNT(column) counts only the rows where that column is {{c2::not null}}.",
  "extra": "The difference is exactly the number of NULLs in the column.",
  "source": "SQL:2016 (ISO/IEC 9075)"
}
```

* `{{c1::...}}`, `{{c2::...}}` — each number becomes its own card. Use different
  numbers for facts you want tested separately, and the same number if you want
  them tested together.
* `{{c1::answer::hint}}` adds a hint shown on the question side.
* `extra` is optional context shown on the answer side. It is not tested.

### HTML

Fields are plain text. `<`, `>` and `&` are escaped for you, so a card may
mention `>` or `<` without breaking rendering. Markup is therefore not
available in card text by design — the note type's CSS does the styling.

---

## 3. What the generator produces

For each deck:

* **Two note types.** `<Deck name> - Recall` with fields `Front, Back, Source,
  Topic`, and `<Deck name> - Cloze` with fields `Text, Extra, Source, Topic`. A
  `<Deck name> - Reverse` note type is included and used only by notes with
  `"reverse": true`.
* **Tags from the topic path.** Topic `Git/Branches and Merging` produces
  `git`, `git::branches-and-merging`. The deck key is added as a tag too.
* **A deck description** recording the note and card counts, the source notes
  file, and the statement that the deck is not official exam material.
* **A `media` manifest**, empty unless you add media support yourself.

Note and card ids are allocated from a per-deck block derived from `deck.key`, so
building several decks and importing them all into one Anki profile cannot cause
an id clash.

---

## 4. Build and check

```bash
# check the notes, then build
python3 generator/validate_notes.py decks/my-deck.notes.json
python3 generator/build_deck.py --input decks/my-deck.notes.json --out decks/my-deck.apkg --deterministic --verify
```

`build_deck.py` runs the linter itself and refuses to write a package when the
notes break a rule. `--skip-lint` overrides that; a deck built with it is a deck
you have not checked.

Useful flags:

| Flag | Effect |
|---|---|
| `--out PATH` | Required. Where to write the `.apkg`. |
| `--deck-name NAME` | Overrides `deck.name` from the file. |
| `--deterministic` | Pins timestamps and normalises the zip container so identical input gives identical bytes. GUIDs are stable with or without it. |
| `--guid-strategy id\|content` | `id` (default) keeps history across text edits; `content` re-creates cards whenever the text changes. |
| `--manifest PATH` | Writes a JSON audit record: deck and note type ids, every GUID, counts, and the file hash. |
| `--verify` | Re-opens the finished `.apkg` and cross-checks counts and GUIDs against the build report. |
| `--skip-lint` | Build even when the notes fail the rules. Not recommended. |

## 5. YAML

YAML input works when PyYAML is installed, and is friendlier for hand editing:

```yaml
deck:
  key: my-topic
  name: My Topic
topics:
  - path: My Topic/Basics
notes:
  - id: mt-001
    topic: My Topic/Basics
    front: What does the first digit of a status code mean?
    back: The class of the response.
    source: RFC 9110
```

Note that `source_claimed` and `reverse` are booleans, and `tags` is a list.
