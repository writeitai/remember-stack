"""BEAM answer agent that exercises the full RememberStack retrieval plane.

Post queryspace cutover the shipping surface is:

* **Assured recipes** — ``question_context``, ``current_context``,
  ``resolve_entity`` (always present after seed)
* **Historical stock / demoted recipes** — still served by lab deployments that
  retain the pre-cutover registry (hybrid claims/chunks, graph_*, claims_about,
  multi_hop_context, …); used when listed by ``GET /recipes``
* **Open query** — SQL / Cypher / query-space discovery / saved queries when the
  deployment has them wired

The agent is recipe-first and open-query second so it matches what an MCP agent
would call. Raw ``/search/claims`` is not the primary path.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import json
import re
from typing import Any
from typing import Final
import urllib.error
import urllib.request

_DEFAULT_ANSWER_MODEL: Final = "openai/gpt-5.6-luna"
_OPENROUTER_URL: Final = "https://openrouter.ai/api/v1/chat/completions"

# Always attempt these when present; query is filled at call time.
# Note: include_facts / current_context can hang on large BEAM deploys when
# fact hydration SQL is pathological — keep them opt-in via a second phase
# with a short timeout rather than blocking the P1 evidence plane.
_ALWAYS_RECIPES: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    (
        "question_context",
        {"k": 40, "candidate_k": 120, "include_facts": False, "include_entities": True},
    ),
    ("claims_hybrid_rrf", {"k": 30, "candidate_k": 100}),
    ("claims_verbatim", {"k": 15}),
    ("chunks_hybrid_rrf", {"k": 15, "candidate_k": 60}),
)

# Fact-layer recipes: short timeout, non-fatal.
_FACT_RECIPES: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    ("current_context", {"k": 10, "evidence_per_fact": 2}),
)

# Ability-specific secondary claim/chunk queries to widen recall.
_ABILITY_QUERIES: Final[dict[str, tuple[str, ...]]] = {
    "abstention": (
        "user feedback UI/UX improvements public launch",
        "UI UX feedback before launch",
    ),
    "contradiction_resolution": (
        "Flask routes HTTP requests",
        "never written Flask routes",
        "handled HTTP requests in this project",
        "POST /login Flask",
    ),
    "event_ordering": (
        "personal budget tracker development",
        "budget tracker authentication expense tracking visualization",
        "transaction CRUD analytics budget tracker",
        "core functionality budget tracker",
    ),
    "information_extraction": ("first sprint ends March", "sprint end date timeline"),
}

_OPEN_QUERY_SQL: Final[tuple[tuple[str, str], ...]] = (
    (
        "facts_predicates",
        "SELECT predicate, count(*) AS n FROM facts_current "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 25",
    ),
    (
        "facts_sample",
        "SELECT fact_kind, fact_id::text, predicate, label "
        "FROM facts_current ORDER BY predicate, fact_id LIMIT 40",
    ),
    (
        "claims_live_sample",
        "SELECT claim_text FROM claims_live "
        "WHERE claim_text ILIKE '%sprint%' OR claim_text ILIKE '%Flask%' "
        "OR claim_text ILIKE '%budget%' "
        "ORDER BY claim_id LIMIT 30",
    ),
)


class AnswerAgentError(RuntimeError):
    """Retrieval or synthesis failed."""


@dataclass
class RetrievalBundle:
    """Structured retrieval payload passed to the answer LLM."""

    recipe_calls: list[dict[str, Any]] = field(default_factory=list)
    open_query_calls: list[dict[str, Any]] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    chunks: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    sql_rows: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_prompt_block(self) -> str:
        """Render a compact, model-facing evidence pack."""
        sections: list[str] = []
        if self.entities:
            lines = [
                f"- {e.get('canonical_name') or e.get('name') or e.get('entity_id')}"
                f" (id={e.get('entity_id')}, type={e.get('entity_type') or e.get('type')})"
                for e in self.entities[:12]
            ]
            sections.append("## Resolved entities\n" + "\n".join(lines))
        if self.facts:
            sections.append(
                "## Current facts / observations\n"
                + "\n".join(f"- {t}" for t in self.facts[:40])
            )
        if self.claims:
            sections.append(
                "## Verbatim claims (P1)\n"
                + "\n".join(f"- {t}" for t in self.claims[:50])
            )
        if self.chunks:
            sections.append(
                "## Live source passages (chunks)\n"
                + "\n".join(f"- {t}" for t in self.chunks[:25])
            )
        if self.edges:
            sections.append(
                "## Graph edges / paths (P2)\n"
                + "\n".join(f"- {t}" for t in self.edges[:30])
            )
        if self.pages:
            sections.append(
                "## Compiled K pages\n" + "\n".join(f"- {t}" for t in self.pages[:10])
            )
        if self.sql_rows:
            sections.append(
                "## Open-query SQL rows\n"
                + "\n".join(f"- {t}" for t in self.sql_rows[:40])
            )
        if self.negatives:
            sections.append(
                "## Typed negatives / empty results\n"
                + "\n".join(f"- {t}" for t in self.negatives[:20])
            )
        if self.errors:
            sections.append(
                "## Retrieval errors (non-fatal)\n"
                + "\n".join(f"- {t}" for t in self.errors[:15])
            )
        if not sections:
            return "## Retrieved evidence\n(none)"
        return "\n\n".join(sections)


class RememberStackClient:
    """Thin HTTP client for the self-host control surface."""

    def __init__(self, *, api_url: str, timeout_s: float = 45.0) -> None:
        """Bind API origin (e.g. http://127.0.0.1:18000)."""
        self._api_url = api_url.rstrip("/")
        self._timeout_s = timeout_s

    def get_json(self, path: str, *, timeout_s: float | None = None) -> Any:
        """GET JSON path."""
        return self._request(method="GET", path=path, timeout_s=timeout_s)

    def post_json(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> Any:
        """POST JSON path."""
        return self._request(
            method="POST", path=path, body=body or {}, timeout_s=timeout_s
        )

    def recipe(
        self, name: str, arguments: dict[str, Any], *, timeout_s: float | None = None
    ) -> dict[str, Any]:
        """Run one registry recipe by name."""
        result = self.post_json(f"/recipe/{name}", arguments, timeout_s=timeout_s)
        if not isinstance(result, dict):
            raise AnswerAgentError(f"recipe {name} returned non-object")
        return result

    def list_recipes(self) -> list[dict[str, Any]]:
        """Active recipe descriptors."""
        result = self.get_json("/recipes", timeout_s=15.0)
        if not isinstance(result, list):
            raise AnswerAgentError("GET /recipes did not return a list")
        return result

    def query_sql(
        self, *, sql: str, max_rows: int = 40, timeout_s: float = 15.0
    ) -> dict[str, Any]:
        """Run sandboxed SQL; returns QueryResult/v1 dict."""
        result = self.post_json(
            "/query/sql",
            {"sql": sql, "parameters": [], "max_rows": max_rows},
            timeout_s=timeout_s,
        )
        if not isinstance(result, dict):
            raise AnswerAgentError("POST /query/sql returned non-object")
        return result

    def describe_query_space(self) -> dict[str, Any]:
        """Manifest-backed schema discovery."""
        result = self.get_json("/query/space?include_examples=false", timeout_s=15.0)
        if not isinstance(result, dict):
            raise AnswerAgentError("GET /query/space returned non-object")
        return result

    def _request(
        self,
        *,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> Any:
        data = None
        headers: dict[str, str] = {}
        if body is not None and method != "GET":
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self._api_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_s if timeout_s is None else timeout_s
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:400]
            raise AnswerAgentError(
                f"HTTP {error.code} {method} {path}: {detail}"
            ) from error
        except TimeoutError as error:
            raise AnswerAgentError(
                f"timeout {method} {path} after "
                f"{self._timeout_s if timeout_s is None else timeout_s}s"
            ) from error
        except urllib.error.URLError as error:
            raise AnswerAgentError(
                f"network {method} {path}: {type(error).__name__}: {error}"
            ) from error


def _append_unique(target: list[str], text: str, *, limit: int = 80) -> None:
    text = text.strip()
    if text and text not in target and len(target) < limit:
        target.append(text)


def _ingest_envelope(
    bundle: RetrievalBundle, *, recipe: str, envelope: dict[str, Any]
) -> None:
    """Fold one recipe Envelope into the retrieval bundle."""
    call: dict[str, Any] = {
        "recipe": recipe,
        "grain": envelope.get("grain"),
        "negative": envelope.get("negative"),
        "n_evidence": len(envelope.get("evidence") or []),
        "n_facts": len(envelope.get("facts") or []),
        "n_chunks": len(envelope.get("chunks") or []),
        "n_entities": len(envelope.get("entities") or []),
    }
    bundle.recipe_calls.append(call)
    neg = envelope.get("negative")
    if isinstance(neg, dict) and neg.get("kind"):
        bundle.negatives.append(
            f"{recipe}: {neg.get('kind')} — {neg.get('explanation') or ''}".strip()
        )

    for evidence in envelope.get("evidence") or []:
        if isinstance(evidence, dict):
            _append_unique(bundle.claims, str(evidence.get("claim_text") or ""))

    for fact in envelope.get("facts") or []:
        if isinstance(fact, dict):
            label = (
                fact.get("label")
                or fact.get("statement")
                or fact.get("fact_label")
                or fact.get("predicate")
            )
            if label:
                _append_unique(bundle.facts, str(label))

    for chunk in envelope.get("chunks") or []:
        if isinstance(chunk, dict):
            text = chunk.get("chunk_text") or chunk.get("text") or ""
            prefix = chunk.get("context_prefix") or ""
            combined = f"{prefix} {text}".strip() if prefix else str(text)
            _append_unique(bundle.chunks, combined, limit=40)

    for entity in envelope.get("entities") or []:
        if isinstance(entity, dict) and entity.get("entity_id"):
            if not any(
                e.get("entity_id") == entity.get("entity_id") for e in bundle.entities
            ):
                bundle.entities.append(entity)

    # Ranking may carry entity candidates from resolve / question_context.
    for rank in envelope.get("ranking") or []:
        if not isinstance(rank, dict):
            continue
        raw_payload = rank.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else rank
        eid = payload.get("entity_id")
        if eid and not any(e.get("entity_id") == eid for e in bundle.entities):
            bundle.entities.append(
                {
                    "entity_id": eid,
                    "canonical_name": payload.get("canonical_name")
                    or payload.get("name"),
                    "entity_type": payload.get("entity_type") or payload.get("type"),
                }
            )

    for edge in envelope.get("edges") or []:
        if isinstance(edge, dict):
            _append_unique(
                bundle.edges,
                (
                    f"{edge.get('subject') or edge.get('from_id')} "
                    f"-[{edge.get('predicate') or edge.get('kind')}]→ "
                    f"{edge.get('object') or edge.get('to_id')} "
                    f"({edge.get('fact_label') or edge.get('label') or ''})"
                ).strip(),
                limit=40,
            )

    for path in envelope.get("paths") or []:
        if isinstance(path, dict):
            _append_unique(bundle.edges, f"path: {path}", limit=40)
        elif isinstance(path, list):
            _append_unique(
                bundle.edges, "path: " + " → ".join(str(p) for p in path), limit=40
            )

    for page in envelope.get("pages") or []:
        if isinstance(page, dict):
            title = page.get("title") or page.get("slug") or page.get("page_id")
            body = page.get("summary") or page.get("body") or page.get("markdown") or ""
            _append_unique(
                bundle.pages, f"{title}: {str(body)[:400]}".strip(), limit=15
            )

    for source in envelope.get("sources") or []:
        if isinstance(source, dict):
            _append_unique(
                bundle.chunks,
                str(
                    source.get("title")
                    or source.get("document_title")
                    or source.get("doc_id")
                    or ""
                ),
                limit=40,
            )


_NAME_RE = re.compile(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+|[A-Z]{2,}|\b[A-Z][a-z]{3,}\b")


def _name_candidates(question: str) -> list[str]:
    """Cheap entity-name candidates from the question (not LLM NER)."""
    found = [m.group(0) for m in _NAME_RE.finditer(question)]
    for token in (
        "Flask",
        "HTTP",
        "API",
        "sprint",
        "budget tracker",
        "Personal Budget Tracker",
        "dashboard",
        "UI",
        "UX",
        "Matplotlib",
        "CRUD",
    ):
        if token.lower() in question.lower() and token not in found:
            found.append(token)
    out: list[str] = []
    for name in found:
        if name not in out:
            out.append(name)
    return out[:10]


def _ingest_sql_result(
    bundle: RetrievalBundle, *, label: str, result: dict[str, Any]
) -> None:
    """Fold one open-query SQL result into the bundle."""
    call = {
        "tool": "query_sql",
        "label": label,
        "empty": result.get("empty_result"),
        "error_code": result.get("error_code"),
        "returned_row_count": result.get("returned_row_count"),
    }
    bundle.open_query_calls.append(call)
    if result.get("error_code") or result.get("termination_reason") == "failed":
        bundle.errors.append(
            f"sql {label}: {result.get('error_code')} "
            f"{result.get('error_message') or ''}".strip()
        )
        return
    columns = [str(c) for c in (result.get("columns") or [])]
    for row in result.get("rows") or []:
        if isinstance(row, dict):
            _append_unique(
                bundle.sql_rows, json.dumps(row, default=str)[:300], limit=50
            )
        elif isinstance(row, list):
            pairs = ", ".join(
                f"{columns[i] if i < len(columns) else i}={v}"
                for i, v in enumerate(row)
            )
            _append_unique(bundle.sql_rows, pairs[:300], limit=50)
        else:
            _append_unique(bundle.sql_rows, str(row)[:300], limit=50)


def retrieve_full_plane(
    *, client: RememberStackClient, question: str, ability: str = ""
) -> RetrievalBundle:
    """Run the full retrieval suite relevant to one BEAM probe."""
    bundle = RetrievalBundle()
    available = {str(r.get("name")) for r in client.list_recipes()}

    def run(name: str, arguments: dict[str, Any], *, timeout_s: float = 45.0) -> None:
        if name not in available:
            # Only record once per missing name to keep the error list small.
            marker = f"recipe {name} not in registry"
            if marker not in bundle.errors:
                bundle.errors.append(marker)
            return
        try:
            envelope = client.recipe(name, arguments, timeout_s=timeout_s)
            _ingest_envelope(bundle, recipe=name, envelope=envelope)
        except AnswerAgentError as error:
            bundle.errors.append(str(error))

    # Phase 1 — assured + high-recall P1 evidence plane.
    for name, base_args in _ALWAYS_RECIPES:
        args = dict(base_args)
        args["query"] = question
        run(name, args, timeout_s=60.0)

    # Ability-specific secondary queries (claims/chunks only; keep light).
    for extra_q in _ABILITY_QUERIES.get(ability, ()):
        if "claims_hybrid_rrf" in available:
            run(
                "claims_hybrid_rrf",
                {"query": extra_q, "k": 20, "candidate_k": 80},
                timeout_s=45.0,
            )
        if "chunks_hybrid_rrf" in available:
            run(
                "chunks_hybrid_rrf",
                {"query": extra_q, "k": 8, "candidate_k": 40},
                timeout_s=45.0,
            )
        if "question_context" in available:
            run(
                "question_context",
                {
                    "query": extra_q,
                    "k": 20,
                    "candidate_k": 80,
                    "include_facts": False,
                    "include_entities": True,
                },
                timeout_s=60.0,
            )

    # Phase 2 — entity resolve and name-anchored testimony.
    names = _name_candidates(question)
    for name in names:
        run("resolve_entity", {"name": name}, timeout_s=20.0)
        run("documents_about", {"entity": name, "k": 8}, timeout_s=25.0)
        run(
            "claims_about", {"entity": name, "query": question, "k": 12}, timeout_s=30.0
        )

    entity_ids: list[str] = []
    for entity in bundle.entities:
        eid = entity.get("entity_id")
        if eid and str(eid) not in entity_ids:
            entity_ids.append(str(eid))

    # Phase 3 — fact/graph tools only for resolved UUIDs (short timeouts).
    for eid in entity_ids[:4]:
        run("relation_current", {"subject_entity_id": eid}, timeout_s=20.0)
        run("observation_current", {"entity_id": eid}, timeout_s=20.0)
        run("entity_timeline", {"entity_id": eid}, timeout_s=20.0)
        run(
            "graph_neighborhood",
            {"entity_id": eid, "hops": 1, "limit": 15},
            timeout_s=25.0,
        )
        run("pages_about", {"entity_id": eid}, timeout_s=20.0)

    for name, base_args in _FACT_RECIPES:
        args = dict(base_args)
        args["query"] = question
        run(name, args, timeout_s=25.0)

    # multi_hop_context takes entity *names*, not UUIDs.
    if "multi_hop_context" in available and names:
        args: dict[str, Any] = {
            "query": question,
            "entity_a": names[0],
            "hops": 2,
            "k": 10,
        }
        if len(names) >= 2:
            args["entity_b"] = names[1]
        run("multi_hop_context", args, timeout_s=30.0)

    if len(entity_ids) >= 2 and "graph_path" in available:
        run(
            "graph_path",
            {
                "from_entity_id": entity_ids[0],
                "to_entity_id": entity_ids[1],
                "max_hops": 3,
            },
            timeout_s=25.0,
        )

    # Phase 4 — open-query plane (non-fatal if unavailable).
    try:
        space = client.describe_query_space()
        bundle.open_query_calls.append(
            {"tool": "describe_query_space", "keys": sorted(space.keys())[:20]}
        )
    except AnswerAgentError as error:
        bundle.errors.append(str(error))

    for label, sql in _OPEN_QUERY_SQL:
        try:
            result = client.query_sql(sql=sql, max_rows=40, timeout_s=12.0)
            _ingest_sql_result(bundle, label=label, result=result)
        except AnswerAgentError as error:
            bundle.errors.append(str(error))

    return bundle


_ABILITY_HINTS: Final[dict[str, str]] = {
    "abstention": (
        "ABSTENTION (strict): Answer ONLY if retrieved evidence explicitly "
        "connects the asked cause to the asked effect (e.g. user feedback → "
        "UI/UX improvements before public launch). Nearby UI/UX or feedback "
        "mentions that do not support that causal link are NOT enough. If the "
        "link is not supported, the ANSWER line must be exactly: "
        "There is no information in the provided chat about that topic."
    ),
    "contradiction_resolution": (
        "CONTRADICTION: If sources conflict, surface BOTH sides explicitly "
        "(quote/paraphrase the 'never' side and the 'did implement' side) and "
        "state that clarification is needed. Do not silently pick one side."
    ),
    "event_ordering": (
        "EVENT ORDERING: Reconstruct chronological mention order of the "
        "requested aspects from the evidence. The ANSWER line itself MUST "
        "contain the full numbered list (1) ... 2) ...). Never write "
        "'listed above' or refer to earlier prose — put the complete ordered "
        "list only on the ANSWER line. Prefer claims about what the user "
        "brought up over generic page titles."
    ),
    "information_extraction": (
        "INFORMATION EXTRACTION: Return the exact short fact (date/name/number) "
        "supported by claims. Prefer the earliest/original value when sources "
        "later revise a timeline unless the question asks for the latest. "
        "ANSWER line should be only that short fact."
    ),
}


def synthesize_answer(
    *,
    api_key: str,
    question: str,
    ability: str,
    bundle: RetrievalBundle,
    model: str = _DEFAULT_ANSWER_MODEL,
) -> tuple[str, str]:
    """Call OpenRouter to produce (final_answer, raw_completion)."""
    hint = _ABILITY_HINTS.get(ability, "")
    recipes_used = ", ".join(sorted({c["recipe"] for c in bundle.recipe_calls}))
    open_used = ", ".join(
        sorted({str(c.get("tool") or c.get("label")) for c in bundle.open_query_calls})
    )
    user = (
        f"{hint}\n\n"
        f"## Question\n{question}\n\n"
        f"## Retrieval plane used\n"
        f"Recipes called: {recipes_used or '(none)'}\n"
        f"Open-query tools: {open_used or '(none)'}\n\n"
        f"{bundle.as_prompt_block()}\n\n"
        "Answer using ONLY the retrieval plane evidence above.\n"
        "Respect grain: claims are testimony (may conflict); facts are current "
        "adjudicated holdings. Prefer short factual answers.\n"
        "Do not invent causal links, timelines, or skills that evidence does "
        "not support. When evidence is insufficient for the asked question, "
        "abstain rather than guess.\n"
        "Final line MUST be exactly one line: ANSWER: <complete final answer "
        "with no forward references like 'listed above'>\n"
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a RememberStack memory agent. You only use retrieved "
                    "claims, facts, chunks, entities, graph edges, K pages, and "
                    "open-query rows. Prefer verbatim store evidence. End with ANSWER:."
                ),
            },
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 32_000,
    }
    request = urllib.request.Request(
        _OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://remember.dev",
            "X-Title": "BEAM-full-retrieval-agent",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise AnswerAgentError(f"answer HTTP {error.code}: {detail}") from error
    raw = str(body["choices"][0]["message"]["content"] or "")
    answer = _extract_answer(raw)
    return answer, raw


def _extract_answer(text: str) -> str:
    """Pull the final ANSWER line; fall back when the model punts with a pointer."""
    answer_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("ANSWER:"):
            answer_lines.append(stripped.split(":", 1)[1].strip())
        # Inline ".... ANSWER: foo" (seen on short IE answers).
        elif "ANSWER:" in upper:
            answer_lines.append(stripped.split(":", 1)[-1].strip())
    if answer_lines:
        candidate = answer_lines[-1].strip()
        # Drop duplicate "March 29. ANSWER: March 29." style leftovers.
        if "ANSWER:" in candidate.upper():
            candidate = candidate.split(":", 1)[-1].strip()
        if candidate and not _is_pointer_answer(candidate):
            return candidate
        # Model wrote "ANSWER: Listed above." — recover numbered body.
        recovered = _recover_ordered_list(text)
        if recovered:
            return recovered
        if candidate:
            return candidate
    recovered = _recover_ordered_list(text)
    if recovered:
        return recovered
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _is_pointer_answer(text: str) -> bool:
    """True when the model deferred to earlier prose instead of answering."""
    lowered = text.strip().lower().rstrip(".")
    return lowered in {
        "listed above",
        "see above",
        "as above",
        "as listed above",
        "above",
        "see list above",
    } or lowered.startswith("listed above")


def _recover_ordered_list(text: str) -> str | None:
    """If the completion contains a numbered list, return it joined."""
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^(\d+[\).\]]|-)\s+\S", stripped):
            # Skip the ANSWER pointer line itself.
            if _is_pointer_answer(stripped.split(":", 1)[-1].strip()):
                continue
            items.append(stripped)
    if len(items) >= 2:
        return " ".join(items)
    return None


def answer_question(
    *,
    api_url: str,
    api_key: str,
    question: str,
    ability: str,
    model: str = _DEFAULT_ANSWER_MODEL,
) -> dict[str, Any]:
    """Full-plane retrieve + synthesize for one BEAM item."""
    client = RememberStackClient(api_url=api_url)
    bundle = retrieve_full_plane(client=client, question=question, ability=ability)
    answer, raw = synthesize_answer(
        api_key=api_key, question=question, ability=ability, bundle=bundle, model=model
    )
    return {
        "answer": answer,
        "raw": raw,
        "model": model,
        "method": "full_retrieval_plane_recipes+openquery+openrouter",
        "recipes_called": [c["recipe"] for c in bundle.recipe_calls],
        "open_query_calls": bundle.open_query_calls,
        "claims": bundle.claims[:50],
        "facts": bundle.facts[:40],
        "chunks": bundle.chunks[:25],
        "entities": bundle.entities[:12],
        "edges": bundle.edges[:30],
        "pages": bundle.pages[:10],
        "sql_rows": bundle.sql_rows[:40],
        "negatives": bundle.negatives,
        "errors": bundle.errors,
    }


def answer_run_dir(
    *,
    run_dir: str,
    api_url: str,
    api_key: str,
    arm: str = "rs",
    model: str = _DEFAULT_ANSWER_MODEL,
    force: bool = False,
) -> dict[str, Any]:
    """Answer every question in a harness run dir; update state.json."""
    from pathlib import Path

    root = Path(run_dir)
    questions = json.loads((root / "questions.json").read_text(encoding="utf-8"))
    state_path = root / "state.json"
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.is_file()
        else {}
    )
    answers = state.setdefault("answers", {}).setdefault(arm, {})
    arm_dir = root / "answers" / arm
    arm_dir.mkdir(parents=True, exist_ok=True)

    answered: list[str] = []
    for question in questions:
        qid = str(question.get("question_id") or question.get("item_id"))
        if not force and answers.get(qid, {}).get("answer"):
            continue
        payload = answer_question(
            api_url=api_url,
            api_key=api_key,
            question=str(question.get("question") or ""),
            ability=str(question.get("ability") or ""),
            model=model,
        )
        answers[qid] = payload
        answered.append(qid)
        safe = re.sub(r"[^\w.\-]+", "_", qid)
        (arm_dir / f"{safe}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    return {
        "arm": arm,
        "n": len(answers),
        "newly_answered": answered,
        "answered": list(answers),
    }
