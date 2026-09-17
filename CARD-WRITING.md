# Writing cards that actually work

Everything in this guide is about one thing: making a card easy to *answer
correctly* and hard to *answer for the wrong reason*.

Spaced repetition is a delivery mechanism. It decides *when* you see a card. It
has no opinion about whether the card is any good. A bad card, reviewed for a
year, teaches you the bad card.

Read this before you edit the decks, and before you write your own notes from a
course or a manual.

---

## 1. One card, one fact

The single biggest fault in shared decks is a card carrying several facts at
once.

**Why it fails.** You are asked to recall four things in one go. You get three
and miss one, so the scheduler marks the whole card wrong and shows it to you
again sooner. The three you knew are now being drilled as if you did not know
them, and the one you missed is not being drilled on its own. The card also
cannot be scheduled sensibly, because its four facts will be learned at
different speeds.

**Before** (one card, four facts):

> **Front:** What are the four Git object types?
> **Back:** Blob (file contents), tree (directory listing), commit (a snapshot
> with parents and metadata), and tag (a named pointer with a message).

**After** (four cards, one fact each):

> **Front:** Which Git object type stores file contents?
> **Back:** Blob.
>
> **Front:** Which Git object type stores a directory listing?
> **Back:** Tree.
>
> **Front:** Which Git object type records a snapshot, its parents and the
> author?
> **Back:** Commit.
>
> **Front:** Which Git object type gives a name, a message and a date to a
> specific commit?
> **Back:** An annotated tag.

The linter's rule **R001** looks for this fault: an answer with more than one
sentence, an enumerated list, or two clauses joined by a semicolon.

**The exception: cloze cards.** A sentence with several blanks is fine, because
each blank becomes its own card. `The three private IPv4 ranges are
{{c1::10.0.0.0/8}}, {{c2::172.16.0.0/12}} and {{c3::192.168.0.0/16}}.` produces
three separate cards, each asking for one range with the others still visible as
context. That is one fact per card.

---

## 2. The prompt must stand on its own

A card is not read in the order you wrote your notes. It appears alone, months
later, with no chapter heading. If it needs context, it is broken.

**Before:**

> **Front:** What does this status code mean?

Nothing tells you which status code. There is no answer.

**After:**

> **Front:** What does the 404 status code mean?
> **Back:** The origin server found no current representation for the target
> resource.

The linter's rule **R002** catches prompts that open with a bare reference word
(`it`, `this`, `that`, `these`, `those`, `they`), prompts that point at context
they do not carry ("as discussed", "the above", "previously"), prompts that are
not question-shaped, and prompts too short to identify their own subject.

Two habits fix almost all of these:

1. **Name the subject in the prompt.** Write "the 404 status code", not "this
   code". Write "when Git rebases a branch", not "when it rebases".
2. **End with a question mark, or start with a cue verb** such as *Define*,
   *Name*, *List*, *State*, *Explain*.

---

## 3. Never word a card negatively

"Which of the following is NOT…", "All of the following except…", "Which
statement is false?" — these formats belong in exams where the answer is
multiple choice and you can reason by elimination. As a flashcard they are
close to useless.

**Why it fails.** A negative card asks you to hold two things at once: the fact,
and the instruction to reverse it. You can know the material perfectly and still
stumble. Worse, a card that has been answered correctly can be answered
correctly for the wrong reason — you may be recalling "the one that sounded odd"
rather than the fact.

**Before:**

> **Front:** Which HTTP method is NOT safe?
> **Back:** POST, PATCH and CONNECT.

**After** (split, and ask for the positive fact):

> **Front:** Which HTTP methods are defined as safe?
> **Back:** GET, HEAD, OPTIONS and TRACE.
>
> **Front:** Is POST a safe method?
> **Back:** No: repeating a POST may change the server's state.

The linter's rule **R003** flags negative wording. It has one deliberate escape
hatch, `"allow_negative": true` on a single note, for the rare case where the
negation *is* the fact — a card about the SQL `EXCEPT` operator, for instance,
where the word is part of the language rather than a way of wording the
question. Using it to silence an awkwardly worded card defeats the purpose.

---

## 4. Keep the answer short enough to be recalled

If the answer is a paragraph, you will memorise the *shape* of the paragraph
rather than its content, and you will fail it the moment the wording changes.

**Before:**

> **Back:** A 301 response indicates that the target resource has been assigned a
> new permanent URI, and any future references to it should use that URI; clients
> following the redirect should update their bookmarks and caches, and the
> Location header carries the new URI, and this differs from 302 where the move
> is temporary…

**After** (one card per idea):

> **Front:** What does a 301 response tell a client about the URL to use in
> future?
> **Back:** The new URL in the Location header is the permanent one.
>
> **Front:** How do 301 and 302 differ in intent?
> **Back:** 301 means the move is permanent, 302 means it is temporary.

The linter's rule **R004** enforces a limit of 220 characters by default, and it
is configurable:

```bash
python3 generator/validate_notes.py my-notes.json --max-answer-chars 160
```

A short answer is also a check on yourself. If you cannot compress the fact to a
sentence, you may not have understood it yet.

---

## 5. Cloze or question and answer?

Both are available. They suit different material.

**Cloze deletion** — you keep the source sentence and blank out a piece of it —
is the better choice when:

* the fact only makes sense *inside* a sentence, such as a protocol step, an
  ordering, or a definition with its qualifier;
* you want the surrounding words as free context, and the context is not part of
  what is being tested;
* the thing to recall is a value *inside* a longer statement: a port number in a
  description, a layer name in a sentence, a clause in a rule.

> TCP opens a connection with a {{c1::SYN}} segment, which the peer answers with
> a {{c2::SYN-ACK}}.

**Question and answer** is the better choice when:

* the prompt and the answer are genuinely different things — a term and its
  meaning, a symptom and its cause, a command and its effect;
* you want to be asked from a direction you choose, for example always
  code → meaning rather than meaning → code;
* there is nothing sensible to blank out, such as "What does a 204 response
  tell a client about the response content?"

A practical test: **cloze works when the sentence is the mnemonic. If you find
yourself writing a long carrier sentence just to hold the blank, use a question
instead.**

---

## 6. Do not let the prompt give the answer away

This is the most common fault in decks that are otherwise well written, and it
makes the card worthless while still feeling productive.

**Before:**

> **Front:** Does a 429 response mean too many requests?
> **Back:** Too many requests.

You can answer from the prompt's grammar alone. You will press *Good* every time
and learn nothing.

**Also before:**

> **Front:** Why does the server send a Retry-After header with a 429?
> **Back:** The server sends a Retry-After header with a 429 to tell the client
> how long to wait.

Every key word of the answer is already in the question. The card tests whether
you can repeat the question back.

**After:**

> **Front:** A client keeps hitting a rate limit. Which response header tells it
> how long to wait, and what does the value mean?
> **Back:** Retry-After, giving seconds to wait before trying again.

The linter's rule **R012** flags a prompt in which most of the answer's key words
already appear, and any prompt that contains the answer verbatim.

A related trap is the answer that is always true, or always false. "Is it
important to check the source?" is not a card. Cards should be answerable from
knowledge, not from plausibility.

---

## 7. Interference: stop similar cards from destroying each other

Two cards that look alike compete. This is called interference, and it is why
you can know both answers and still get them mixed up under pressure.

**Before** (three prompts that differ only in a number):

> What is the difference between 401 and 403?
> What is the difference between 402 and 403?
> What is the difference between 404 and 403?

**After** (each prompt points at something distinct):

> What separates 401 from 403 in practice?
> Which status code is reserved and unused?
> A client is told a resource is gone for good. Which code is that?

Three things reduce interference:

1. **Make the prompt's distinguishing feature prominent.** Put the thing that
   differentiates the card at the start or end of the prompt, not buried in the
   middle. "Which status code is reserved and unused?" is distinctive;
   "Which status code, among the client error codes, is the one that is not
   used?" is not.
2. **Vary the wording** between cards in the same family, so the shape of the
   prompt is not itself a cue.
3. **Do not create reverse cards by reflex.** A code → meaning card and its
   reverse are a classic interference pair, especially when several codes
   produce similar meanings. The generator supports `"reverse": true` per note;
   use it only when the reverse direction has exactly one answer.

The linter reports **same-stem families** as an advisory: groups of cards whose
prompts are nearly identical but whose answers differ. This is not an error —
a deck of status codes has to contain cards that look alike — but it is worth
seeing, because a large family is where mixing up answers starts. Run:

```bash
python3 generator/validate_notes.py decks/http-status-codes.notes.json
```

and read the ADVISORY lines.

---

## 8. When to split a card

Split when the card fails for a reason that is not one fact:

| Symptom | What it means | What to do |
|---|---|---|
| You get it right sometimes and wrong other times | Two facts at different strengths on one card | Split into two cards |
| You keep failing the same card | The card is too big, ambiguous, or badly worded | Rewrite it, then split if it still fails |
| You can answer it, but slowly, every time | The answer contains a list | One card per item |
| You get the right answer but for the wrong reason | The prompt is guessable or leaky | Reword the prompt |
| You mix it up with a similar card | Interference | Reword one of them, or make the difference the question |

The honest advice, from the *Anki* side of the process: **a card you keep
failing should be rewritten, not ground down.** Pressing *Again* ten times
teaches you that the card is unpleasant. Editing the card so that it asks one
clear thing teaches you the fact. See `docs/USING-THE-DECKS.md`.

---

## 9. Write the source on the card

A fact without a source cannot be checked, and a deck you cannot check is a deck
you have to trust blindly.

The decks in this pack use a `Source` field shown on the answer side, and the
linter's rule **R007** requires a source on every note unless the note explicitly
declares `"source_claimed": false`.

Good sources are specific: `RFC 9110`, `PostgreSQL 16 manual, section 4.2`,
`Course notes, week 3, slide 12`. "The internet" is not a source. When a fact
turns out to be wrong or out of date, the source is what lets you find out
quickly — and it is the difference between a deck that ages well and one that
quietly misleads you.

**Prefer one authoritative source per topic and cite it consistently.** If two
sources disagree, that disagreement is itself worth a card, with both sources
named.

---

## 10. A checklist before you build

* One fact per card.
* The prompt names its own subject and ends in a question mark.
* No "NOT", no "except", no "which is false".
* The answer fits in about two lines.
* The prompt does not contain the answer.
* The answer cannot be guessed from the wording.
* Similar cards are worded differently enough to be told apart.
* Every card carries a source.
* The card is something you actually need to know — not trivia you collected
  because it was in the chapter.

Then run the linter and the build:

```bash
python3 generator/validate_notes.py decks/my-deck.notes.json
python3 generator/build_deck.py --input decks/my-deck.notes.json --out decks/my-deck.apkg --deterministic
```
