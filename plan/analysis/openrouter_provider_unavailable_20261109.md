# Explicit OpenRouter unavailability with an allocated request ID

This is non-binding incident evidence. The retry contract is specified in
[the provider-rotation design](../designs/openrouter_provider_rotation_design.md),
section3.

A native ingestion request received HTTP200 with the following redacted shape:

```json
{
  "id": "gen-unavailable-attempt",
  "error": {
    "code": 502,
    "message": "The provider could not process this request.",
    "metadata": {"error_type": "provider_unavailable"}
  }
}
```

There were no `choices` or `usage` fields. The adapter did not recognize this
structured rejection as unbilled because an ID was present and the message was
not the existing Flex admission phrase. It entered `recover_completion_usage`
in `src/rememberstack/adapters/openrouter.py`. Five metadata reads returned404;
the worker correctly stopped with `ProviderAccountingError` rather than inventing
usage. No answer generation was involved in this incident.

[OpenRouter's billing FAQ](https://openrouter.ai/pricing), retrieved2026-11-09,
says failed model attempts do not incur model-token charges; already-executed
auxiliary services can still be charged. The structured model adapter does not
request those auxiliary services. The
[zero-completion policy](https://openrouter.ai/blog/announcements/never-pay-for-empty-ai-responses-again/),
retrieved2026-11-09, separately describes its protections for empty/error results.
Neither source makes a transport timeout proof of a rejected request, and neither
justifies discarding a response's existing usage or choices.

The narrowly supported distinction is an explicit transient error envelope whose
`error.metadata.error_type` equals `provider_unavailable`, with neither choices
nor usage. Like the existing Flex rejection, its allocated request ID does not
make it a completed generation. It can enter the existing bounded retry/rotation
path. Generic ID-bearing errors, malformed completions, authentication failures
and unavailable accounting for potentially completed work remain fail-closed.
No zero-cost usage record is fabricated; the rejection stays a transport event,
and the successful attempt supplies ordinary provider-reported accounting.

The reproduction and accounting boundaries are covered by
`src/tests/adapters/test_openrouter.py`. This changes transport classification
only: no prompts, model choices, schemas, document content, processing generations
or paid-product reuse identities change. In particular, D56 requires no re-key
or re-extraction of already completed documents.
