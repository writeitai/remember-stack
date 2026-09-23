---
title: What is a memory system?
description: Why AI assistants forget, why searching your documents is not the same as remembering, and what a memory system does about it. No technical background needed.
applies_to: [remember.dev, self-hosted]
---

# What is a memory system?

This page is for anyone, technical or not. It explains the problem a memory
system solves and what one does. If you already build AI agents, you can
skip to [Why RememberStack](../index.md).

## Two kinds of assistant

Imagine two assistants helping your team.

The first is new every morning. Before each task they read whatever files
you hand them, work quickly and well, and then forget everything overnight.
Tomorrow you hand them the files again. If you forget to include last
week's decision, they don't know it happened. If two files disagree, they
pick one without telling you. If you ask "who is in charge of this?", they
answer from whichever file they happened to read, even if it is a year out
of date.

The second has been on the team for a year. They remember what was decided
and when. They know that the plan changed in March and why. When two people
told them different things, they tell you so. When they don't know, they
say "I don't know" rather than guessing. And when you ask how they know
something, they can point you to the email or the meeting where it came up.

Today's AI assistants are the first kind. A memory system turns them into
the second.

## Why AI assistants forget

An AI model does not learn from your conversations. Everything it knows
about your work has to be put in front of it, as text, every time you ask
something. That text is called the *context*, and it has a size limit. When
the conversation ends, the context is thrown away.

So every session starts from zero. The usual workarounds are to paste the
same notes in again, to keep ever-longer instructions, or to let the
assistant search your files. Each helps a little. None of them is memory.

## Why searching your documents is not remembering

The most common fix is to let the assistant search your documents and read
the passages that look most relevant. It helps, and it has three blind
spots.

**It cannot tell old from new.** Search finds what *sounds* like your
question. The plan from January and the correction from June both sound
like "when does the project launch?". The assistant gets both, with nothing
to say which one still holds.

**It cannot tell what is true from what was said.** A document records what
someone said at one moment. People change their minds, correct each other,
and get things wrong. Search treats every sentence as equally current.

**It always answers.** Ask about something that was never written down and
search still returns the closest text it can find. The assistant then
answers from that, confidently, and nobody notices the gap.

## What a memory system does

A memory system reads your documents the way a careful colleague would, and
keeps four things that search loses.

**What each source said.** Every statement worth keeping is stored with the
exact sentence it came from and the date it was said. Nothing is rewritten
or thrown away.

**What is true now.** From everything that was said, the memory works out
what currently holds. When a newer source changes something, the memory
updates, and the older statement stays on record as history. When two
sources disagree, the memory keeps both and marks the disagreement.

**When it was true.** Facts carry dates. You can ask what is true today,
what was true last spring, or how something changed over the year.

**Where it came from.** Every answer points back to the sentence, in the
document, that supports it. You can check it in seconds.

With these, an assistant can answer "who leads the project?" with the
current answer, say since when, name the meeting where it was decided, and
mention that one older document still says otherwise.

## An example

Here is how a small team's memory changes over a few months.

| Date | What the documents say | What the memory holds afterwards |
|---|---|---|
| January | Kickoff notes: "Ravi leads the billing migration. Launch in June." | Ravi leads the billing migration, since January. Launch planned for June. |
| April | Planning doc: "Launch moves to October; the invoice system needs a rewrite." | Launch planned for October, since April. The June date is kept as history, with the kickoff notes as its source. |
| June | Retro: "Ravi moved to the search team on 1 June. Dana now leads billing." | Dana leads the billing migration, since 1 June. Ravi led it from January to 1 June. |
| July | A slide deck from February is added: "Ravi leads billing." | The slide is kept as something a source said in February. It supports Ravi's January to June period; it does not change who leads now. |

Ask "who leads the billing migration?" in August and the answer is Dana,
since 1 June, according to the June retro. Ask "who led it in March?" and
the answer is Ravi. Ask "when was the launch?" and you get October, with a
note that it was June until April.

A search over the same four documents would return all of them and leave
the rest to chance.

## Where a memory system helps

- **Teams.** Decisions, owners and plans that survive staff changes and
  long projects, without anyone maintaining a wiki by hand.
- **Personal assistants.** An assistant that knows your commitments,
  preferences and history across months of email, notes and messages.
- **Customer-facing agents.** Support and account agents that know each
  customer's history, and which of the things a customer said last year
  still hold.
- **Coding agents.** Agents that remember why the code is the way it is:
  the decision, the constraint, the incident that caused it.
- **Research and analysis.** Keeping track of which source claimed what,
  where sources disagree, and how the picture changed over time.

## How it compares to things you know

| | What it keeps | What it cannot do |
|---|---|---|
| **Chat history** | The conversation, word for word | Tell what still holds; survive beyond one conversation or one tool |
| **Notes or a wiki** | Whatever someone writes down | Update itself when things change; point back to where each line came from |
| **Search over documents** | The documents | Tell old from new, said from true, or known from unknown |
| **A vector database** | The documents, indexed by meaning | The same as search: it finds similar text, not current facts |
| **A memory system** | What was said, what is true now, when, and where it came from | Replace your documents: it works from them, so you keep them |

## RememberStack

RememberStack is a memory system for AI agents.

<p class="lead">Centralize all your information in a single place.<br>
Expose that information to AI agents in the best possible way.</p>

It is open source, and remember.dev runs it for you. Continue with
[Why RememberStack](../index.md), or take the
[five-minute tour](how-it-works.md) to see what happens to a document once
you send it.
