# RememberStack

[![CI](https://github.com/writeitai/remember-stack/actions/workflows/ci.yml/badge.svg)](https://github.com/writeitai/remember-stack/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-remember.dev-ee5b44)](https://remember.dev/docs)
[![PyPI](https://img.shields.io/pypi/v/remember)](https://pypi.org/project/remember/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**Memory engine for AI agents**

Process anything relevant: email, chat, specs, meeting notes, tickets, images,
video, audio.

The information is organized and stored.

Your agents have easy access to the information - they remember!

**[Read the docs](https://remember.dev/docs)** ·
**[Quickstart](https://remember.dev/docs/start/quickstart)** ·
**[What is a memory system?](https://remember.dev/docs/start/what-is-a-memory-system)**

**89.94% accuracy on LoCoMo benchmark.**

<br />

<p align="center">
  <img src="website/public/brand/memory-graph.svg" alt="RememberStack memory graph: sources resolve into connected facts" width="520" />
</p>

---

## Main features

- **What each source said.** Every statement worth keeping is stored as a
  claim, with the exact passage it came from and the date it was said.
  Claims are never edited.
- **Who and what it is about.** Names become entities. "John", "John S."
  and "our backend lead" can resolve to one person, while two people called
  Dana stay two. Each entity keeps every spelling the sources used and a
  short profile, and a merge can be undone.
- **What is true now.** From those claims it maintains facts about the
  people, systems and decisions in your work. When a newer source changes a
  fact, the fact changes and the old one stays on record. When two sources
  disagree, you see both, marked as a contradiction.
- **When it was true.** Facts carry the period in which they held. Ask what
  is true today, what held last March, or how something changed.
- **Where it came from.** Every fact links to its claims, every claim to the
  characters in the source. Your agent can quote and cite; you can check.

<p align="center">
  <img src="website/public/docs/diagrams/claims-and-facts.svg" alt="Claims (what sources said) feed facts (what is true now)" width="820" />
</p>

## Built for agents

- **CLI.** `pip install remember` gives you the `remember` command: send
  documents (`remember ingest`), ask (`remember query`) and connect your
  agent (`remember setup`). [CLI →](https://remember.dev/docs/reference/cli)
- **MCP.** `remember mcp` gives Claude Code, Cursor, Codex, Claude Desktop and
  other MCP clients the memory tools; `remember setup` configures them in one
  command. [Connect your agent →](https://remember.dev/docs/start/connect-your-agent)
- **Python SDK.** The same package as a Python client, `remember.Client`.
  [SDK →](https://remember.dev/docs/reference/python-sdk)
- **HTTP API.** The same operations over plain HTTP.
  [API →](https://remember.dev/docs/reference/http-api)
- **Your files.** Markdown, text, HTML, Word, PowerPoint and Excel convert out
  of the box; PDFs and images with an OCR converter.
  [File types →](https://remember.dev/docs/guides/file-types)

## Quickstart

**[Quickstart →](https://remember.dev/docs/start/quickstart)**

Or paste this into your coding agent:

```text
Set up RememberStack memory for me. Follow the quickstart at
https://remember.dev/docs/start/quickstart (all docs for agents:
https://remember.dev/llms.txt). Ask me whether to use remember.dev or a
self-hosted instance, install the `remember` client, connect yourself with
`remember setup`, then send one document and answer a question from it,
citing the source.
```

## Open source, all of it

Apache-2.0. Everything that decides what your memory holds (extraction,
entity resolution, supersession, provenance, forgetting) is in this
repository. Nothing that affects correctness is held back.

- Docs: [remember.dev/docs](https://remember.dev/docs)
- Current release: [v0.19.1](https://github.com/writeitai/remember-stack/releases/tag/v0.19.1) ·
  [PyPI](https://pypi.org/project/remember/) ·
  [container](https://github.com/writeitai/remember-stack/pkgs/container/remember-stack)
- How it works inside: [architecture](https://remember.dev/docs/concepts/architecture) ·
  [design corpus](plan/README.md) · [decision log](decisions.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CLA.md](CLA.md). Pull requests need
the contributor-agreement checkbox in the PR template.
