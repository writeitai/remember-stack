# OpenRouter success replies whose body is not a JSON object

This note is non-binding analysis. The retry and accounting rules it relies on
are in [the provider-rotation design](../designs/openrouter_provider_rotation_design.md),
section 3.

## What prompted it

A retained LoCoMo benchmark run (trial 5) stopped after 22 saved answers.
The only error was `Expecting value: line 27 column 1 (char 143)`. Python's
JSON decoder gives this message when no JSON value starts at that offset:
either the input ends there, or the character there cannot begin a JSON value
(for example `<` or a letter). The message does not say which. It also does
not show what the first 143 characters were, so it says nothing about whether
the body was whitespace, a gateway page or something else. The
raw HTTP body and the full stack trace were not recorded. This note therefore
does **not** identify the cause of that stop. It describes an adapter gap found
while investigating it. Closing the gap would make a similar event diagnosable.

## The gap

`src/rememberstack/adapters/openrouter.py` checks error statuses and capacity
envelopes before it reads a reply. A reply with a success status was then read
with a bare `response.json()` on two paths:

- `_post`, used by embeddings and by chat calls with no provider rotation;
- `_completion_text_rotating`, used by chat calls with a provider shortlist.

When the body was empty, only whitespace, not JSON, or JSON that is not an
object (for example `[]` or `null`), the result was an untyped error: a raw
`json.JSONDecodeError`, or later an `AttributeError` on `.get`. It escaped the
adapter's typed `OpenRouterProviderError` contract. It had no endpoint, status
or provider host. `generate()` did not report it to the generation recorder,
because that recorder only catches typed errors.

The separate checks on completion *content* (the model's JSON answer inside a
valid reply) and on structured output were never affected. They run after the
reply body has decoded.

## The correction

Both paths now share one decoder, `_success_body`. A success reply that is not
a JSON object raises the ordinary `OpenRouterProviderError`:

- The message gives the endpoint, the status, the body size in bytes, a body
  class (`empty`, `whitespace_only`, `not_json`, or the JSON type of a non-object),
  and the decoder's fixed reason and character offset. It never includes body
  text, headers or credentials, because a partial reply can contain model
  output derived from user content. The error is raised after the decoder's
  `except` block, so no decoder exception is attached as its cause or context.
  That matters because a `json.JSONDecodeError` keeps the whole raw body in its
  `doc` attribute and its own message would appear in a printed traceback.
  This protects the message and the exception chain only. It is not memory
  redaction: the HTTP response object still exists in the callers' frames.
- On the rotation path the error carries the targeted provider slug, like
  every other error on that path.
- `usage` is `None`, which means unknown, not zero. A success status does not
  prove the request was free. For the same reason the error is never marked
  `retryable_unbilled`, and the adapter does not retry, rotate or start a new
  retry budget. Callers handle it as an ordinary provider error under their
  existing retry or fallback policy, as for any other transport error.
- It is a plain provider error, not `OpenRouterInvalidResponseError`. For
  embeddings, an invalid-response error would mark a chunk as poison (content
  that can never embed). A broken reply says nothing about the chunk, so it is
  treated as a provider outage. The E1 worker already treated the old untyped
  error the same way.

Valid replies, including valid JSON preceded by whitespace, decode exactly as
before. The rule changes no reuse key, prompt, schema, model, routing or price
(D56).

## Where the rule is recorded

The same change adds this rule to section 3 of the
[provider-rotation design](../designs/openrouter_provider_rotation_design.md)
and documents it for operators on the self-hosting models page
(`website/src/app/docs/self-hosting/models/page.mdx`). It restores the
adapter's existing typed-error contract. It adds no recovery mechanism, so no
new numbered decision is needed.
