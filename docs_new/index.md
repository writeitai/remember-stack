---
title: Why RememberStack
description: Memory for AI agents that keeps what each source said, what is true now, and when, with the path back to the source.
applies_to: [remember.dev, self-hosted]
---

# Give your agents a past

Close the chat and the work is gone. The next session opens on an empty
window: the agent does not know which decision still holds, which file said
it, or what changed overnight. You paste the same notes in again, or the
model fills the gap with something plausible.

RememberStack is memory for that agent. You give it the documents your work
already produces: specs, notes, tickets, meeting transcripts, chat history.
It reads them, keeps what matters, and answers your agent's questions with
facts it can trace back to the source.

*RememberStack is the open-source memory engine. `remember` is its Python
client and CLI. remember.dev runs RememberStack for you.*

## What RememberStack keeps that a context window loses

**What each source said.** Every statement worth keeping is stored as a
claim, together with the exact passage it came from and the date the source
said it. Claims are never edited. If a spec from March said the billing
migration would ship in June, that stays on record even after the plan
changes.

**What is true now.** From those claims, RememberStack maintains facts about
the people, systems and decisions in your work. When a newer source changes
a fact, the fact changes, and the claim it replaced stays on record. When two
sources disagree, you see both, marked as a contradiction.

**When it was true.** Facts carry the period in which they held. You can ask
what is true today, what was true on a given date, what held during a
quarter, or how something changed over time.

**Where it came from.** Every fact links to the claims that support it, and
every claim to the characters in the source document. Your agent can quote
and cite. You can check.

## What your agent gets back

When your agent asks a question, RememberStack does not return the passages
most similar to the question. It returns the facts that answer it, each with
its time window, the evidence behind it, and any contradiction, in one typed
result that is ready to put into a prompt.

If the memory has nothing on the subject, the result says so. It does not
hand back the closest text it could find. If a name could mean two different
people, it says that too. An agent that is told "unknown" stops inventing.

No language model is called while answering. The same question against the
same memory returns the same result, fetching it costs nothing in model
calls, and you can inspect exactly why each item is there.

## Why not a vector database

A vector search finds the passages that sound most like your question. It
cannot tell you which of them is still current, which one a later document
corrected, or whether two of them contradict each other. It has no idea when
anything was true. And it always returns something, even when nothing
relevant exists.

RememberStack uses vector search as one signal among several. The others
are keyword search, the graph of how entities relate, and time. On top of
retrieval, it keeps a recorded judgment of what is currently held true and
why.

## Two ways to run it

**remember.dev** runs RememberStack for you. Each project gets its own
isolated deployment with its own database, storage and HTTPS endpoint. You
install the client and start sending documents. Reading from memory is not
charged.

**Self-hosted** means you run the same engine on your own infrastructure.
It is open source under Apache-2.0. Everything RememberStack does with your
documents is in the public repository, nothing is held back for the managed
service, and nothing about how memory works changes between the two.

[Compare remember.dev and self-hosted](start/choose.md)

## Start here

- [A five-minute tour](start/how-it-works.md): what happens to a document
  from the moment you send it to the moment your agent asks about it.
- [Quickstart](start/quickstart.md): send a document and ask your first
  question.
- [Connect your coding agent](start/connect-your-agent.md): give Claude Code,
  Cursor, Codex or Claude Desktop access to your memory.
- [Time](concepts/time.md): the idea that sets RememberStack apart from
  retrieval over text.
