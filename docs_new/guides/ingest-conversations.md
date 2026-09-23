---
title: Ingest conversations and transcripts
description: Turn chat sessions and meeting transcripts into documents RememberStack can date, attribute and cite turn by turn.
applies_to: [remember.dev, self-hosted]
---

# Ingest conversations and transcripts

Close the chat and the conversation is gone, along with every decision made
in it. This page shows how to keep conversations: chat sessions with an
agent, meeting transcripts, message threads. RememberStack has no separate
conversation API. You render each conversation as a Markdown document and
ingest it like any other file. The format below keeps who said what, and
when, in a form the extractor reads reliably.

Setup (endpoint and token) is in the [Quickstart](../start/quickstart.md).

## One document per session

Render one Markdown document per session (one meeting, one chat, one day
of a thread). Put one line per turn, in this shape:

```text
[<turn-id> | <timestamp>] <speaker>: <text>
```

For the billing migration stand-up:

```markdown
# Billing migration stand-up — 2026-09-17

Participants: Dana, Ravi

[t1 | 2026-09-17T09:30:00Z] Dana: Where are we on the invoice exporter?

[t2 | 2026-09-17T09:31:10Z] Ravi: It needs a rewrite. The migration moves from June to October.

[t3 | 2026-09-17T09:32:05Z] Dana: Agreed. I will tell finance today.
```

Why this shape:

- **The turn ID** gives every statement a stable anchor. Evidence points to
  character positions in the document; a turn ID in the quoted passage lets
  you find the turn again.
- **The timestamp on every line** puts each statement's own time next to
  it in the text, where the extractor can read "next week" in turn 40
  against turn 40's time rather than the start of the meeting.
- **The speaker name** on every line lets each claim be attributed to the
  person who said it.
- **A blank line between turns** keeps turns apart when the document is cut
  into sections and chunks.

Write timestamps in UTC with a zone (`Z` or `+00:00`). If your source has
no time zone, say so in a header line rather than guessing silently.

This is the format RememberStack's own long-conversation benchmark uses
(`render_session` in `benchmarks/locomo/protocol.py`).

## Render and ingest a session

```python
from dataclasses import dataclass
from datetime import UTC, datetime

import remember


@dataclass
class Turn:
    turn_id: str
    at: datetime
    speaker: str
    text: str


def render_session(title: str, participants: list[str], turns: list[Turn]) -> str:
    lines = [f"# {title}", "", f"Participants: {', '.join(participants)}"]
    for turn in turns:
        stamp = turn.at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        text = " ".join(turn.text.split())  # one line per turn
        lines += ["", f"[{turn.turn_id} | {stamp}] {turn.speaker}: {text}"]
    return "\n".join(lines) + "\n"


turns = [
    Turn("t1", datetime(2026, 9, 17, 9, 30, tzinfo=UTC), "Dana",
         "Where are we on the invoice exporter?"),
    Turn("t2", datetime(2026, 9, 17, 9, 31, 10, tzinfo=UTC), "Ravi",
         "It needs a rewrite. The migration moves from June to October."),
    Turn("t3", datetime(2026, 9, 17, 9, 32, 5, tzinfo=UTC), "Dana",
         "Agreed. I will tell finance today."),
]

client = remember.Client.from_env()
body = render_session("Billing migration stand-up — 2026-09-17", ["Dana", "Ravi"], turns)
version = client.ingest(
    body.encode("utf-8"),
    filename="standup-2026-09-17.md",
    mime="text/markdown",
    title="Billing migration stand-up — 2026-09-17",
    source_kind="meeting",
    source_ref="standup/2026-09-17",
    source_modified_at=turns[0].at,
)
print(version.version_id, version.created)
```

Three choices in that call matter:

- **`source_kind` + `source_ref` name the conversation.** Use your own
  stable ID for it: a meeting ID, a chat session ID, a thread ID plus a
  date. The same pair later means "the same conversation".
- **`source_modified_at` is the session time.** Use the session's start
  time (or its end time, consistently). Every claim extracted from the
  document gets it as `asserted_at`, the time the source made the
  statement. Never leave it as the moment you happened to upload.
- **`mime="text/markdown"`.** Bytes carry no file extension to guess from.

The same file from the CLI:

```bash
remember ingest standup-2026-09-17.md \
  --mime text/markdown \
  --source-kind meeting --source-ref standup/2026-09-17 \
  --source-modified-at 2026-09-17T09:30:00+00:00
```

From an agent over MCP, pass the rendered text:

```json
{
  "name": "ingest",
  "arguments": {
    "text": "# Billing migration stand-up — 2026-09-17\n\nParticipants: Dana, Ravi\n\n[t1 | 2026-09-17T09:30:00Z] Dana: Where are we on the invoice exporter?\n",
    "filename": "standup-2026-09-17.md",
    "mime": "text/markdown",
    "source_kind": "meeting",
    "source_ref": "standup/2026-09-17",
    "source_modified_at": "2026-09-17T09:30:00+00:00"
  }
}
```

## Conversations that keep growing

A meeting ends; a chat with an agent or a support thread may not. You have
two shapes to choose from.

**Close sessions and start new documents (recommended).** Cut the
conversation into sessions (per day, per topic, per agent run) and ingest
each finished session once, with its own `source_ref`, in the default
`snapshot` mode. Each session stays dated testimony forever. Nothing is
reprocessed, and a question about "what Ravi said on Tuesday" has a
document that is exactly Tuesday.

**Re-send one growing document in `living` mode.** If you must keep one
document per conversation, send the full rendered conversation each time it
grows, with the same source pair and `versioning_mode="living"`:

```python
version = client.ingest(
    body.encode("utf-8"),
    filename="support-4411.md",
    mime="text/markdown",
    source_kind="chat",
    source_ref="support/4411",
    source_modified_at=last_turn_at,
    versioning_mode="living",
)
```

Each send is a new version. Unchanged passages reuse their earlier
extraction. In living mode the latest
version is the conversation's standing statement: if you ever send a
version with turns removed, facts that rested only on those turns are
closed. Set `source_modified_at` to the time of the newest turn.

The cost of this shape: claims extracted from a new version take that
version's `source_modified_at` as their `asserted_at`. Passages that are
unchanged, with unchanged neighbours, keep the claims and dates they
already had; passages that are re-read get the newer date, even for old
turns. Closed sessions keep every date exact, which is why they are the
recommended shape.

Choose the mode on the first send. A document keeps the mode it was
created with; a later `versioning_mode` for the same source pair is
ignored. [Keep a source up to date](keep-sources-current.md) and [Updating
a source: snapshot and living](../concepts/updating-sources.md) explain the
difference in full.

## Transcripts from audio or video

RememberStack does not ingest audio yet. Transcribe the recording with a
tool of your choice, render the transcript in the turn format above using
the recording's clock (or offsets added to the recording's start time), and
ingest the Markdown.

## Next

- [Wait until a document is queryable](wait-for-readiness.md)
- [Ask about the past](ask-about-the-past.md): questions like "what did we
  decide in the June meetings?"
- [Cite the source of an answer](cite-sources.md)
