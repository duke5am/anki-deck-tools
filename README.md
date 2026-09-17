# anki-deck-tools

Turn structured notes into a well-formed Anki deck — and **lint the notes** so the
cards are actually learnable.

Two tools and two ready-made decks. `genanki` for generation, nothing else.

```bash
pip install genanki            # or: python3 -m venv .venv && .venv/bin/pip install genanki

python3 generator/validate_notes.py decks/http-status-codes.notes.json
python3 generator/build_deck.py decks/http-status-codes.notes.json --out out.apkg
```

## The linter is the useful half

Most shared decks are bad because of how the cards are *written*, not how they are
packaged. A card with five facts on it is unlearnable. A prompt that needs outside
context is a coin flip. An answer you can guess from the grammar teaches nothing.

```
bad-R001: R001 one-fact-per-card: answer states 2 separate sentences, so it is more than one fact
bad-R002: R002 unambiguous-prompt: prompt opens with a bare reference word (it/this/that/...)
bad-R003: R003 no-negative-wording: negative wording in prompt: 'Which HTTP status code is not'
bad-R004: R004 answer-too-long: answer is 295 characters, over the 220 limit
bad-R005: R005 duplicate-or-near-duplicate: same prompt and same answer as note bad-R005
bad-R006: R006 orphan-card: note has no topic, so it cannot be filed or tagged
bad-R007: R007 missing-source-attribution: note claims a source but none is given
bad-R009: R009 malformed-cloze: cloze note has no {{c1::...}} marker
bad-R010: R010 duplicate-or-missing-note-id: id 'bad-R010' is already used at position 10
bad-R011: R011 invalid-tag: tag 'rate limit' contains a space
bad-R012: R012 prompt-leaks-answer: 100% of the answer's key words already appear in the prompt
```

Twelve rules, each reported with the note id and the reason. Exit code 1 so it
works in a pre-commit hook.

`R005` is deliberately two-tiered: "What does a 400 mean?" and "What does a 412
mean?" are legitimate same-stem cards, so only *answer* similarity is a violation;
same-stem families surface as **advisories**. Flagging them as errors would fire
31 times on a good status-code deck and train you to ignore the rule.

## The decks

Two real decks, generated from the source notes in this repo:

| Deck | Notes | Cards |
|---|---:|---:|
| HTTP Status Codes and Semantics | 128 | 139 |
| Git Concepts and Commands | 141 | 151 |

Cloze notes with several blanks produce one card per blank, which is why cards
exceed notes. Each `.apkg` ships with a `manifest.json` recording every GUID, so
you can diff what you have against what the source produces.

**Verified:** each `.apkg` was opened as a zip and its SQLite collection read out
to confirm the note count, card count and distinct GUIDs. They are real importable
artifacts, not files that merely exist.

## Why generated beats a static download

Regenerating with **stable GUIDs** preserves your scheduling history — the cards
you have already learned stay learned. Downloading a fresh copy instead resets
everything. `build_deck.py` derives each GUID from the note's id, so editing a
card's text does not orphan it:

```bash
python3 generator/build_deck.py notes.json --out deck.apkg --deterministic
```

**A caveat worth knowing:** byte-identity across runs is not portable across
different Python or zlib versions, so the durable guarantee is **stable GUIDs**,
not identical files. Deleting a note from the source also does not delete the card
in Anki — Anki never deletes on import — so use the manifest diff to find orphans.

`CARD-WRITING.md` covers the minimum-information principle, when cloze beats
question/answer, and how to write a prompt whose answer is unambiguous.

## What these decks are not

Original study material, **not official certification material**, and not
affiliated with or endorsed by any certification body. Always check the official
exam objectives. Cards are only as good as their source — verify anything that
matters. And spaced repetition only works if you actually do it.
