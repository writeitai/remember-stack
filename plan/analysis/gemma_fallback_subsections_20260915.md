# Gemma fallback wire field: subsections

On the reconstructed conv-42 D1 fallback request, Gemma/Vertex completed when
the recursive JSON field was named `subsections` instead of `children`. This
note records that exact-input measurement. It does not change the D79
anchor-tree contract, and it does not claim that every hang or HTTP failure
shares this cause.

Durable probe record:
[locomo-conv42-gemma-structure-probes-20260915.json](https://github.com/writeitai/ultimate-memory-cloud/blob/9520a426f7ac4270d15409903f7426e9fad4a5cb/design/analysis/locomo-conv42-gemma-structure-probes-20260915.json).
Interrupted diagnostic streams were stopped after the behavior was established;
their usage is unavailable and those charges remain unknown.

## What that exact request showed

The fallback schema requires `anchor`, `occurrence_index`, and a nested list on
every node. On this prompt, Vertex's constrained decoder emitted keys
alphabetically: `anchor`, `children`, `occurrence_index`. After closing the
nested list it padded whitespace instead of writing the remaining required
index. The JSON never closed. `propertyOrdering` did not change the order.

A one-field structured call on the same route succeeded. The unchanged
root-summary schema later succeeded on a separate retry. Those facts support
continuing to use this route; they do not recover the earlier lost HTTP
statuses from the 851s / 0.29s failures, and they do not prove every hang is a
field-name problem.

## What worked on this input

Renaming only that nested field to `subsections` completed in 6.3s with valid
JSON. Alphabetical emission then becomes `anchor`, `occurrence_index`,
`subsections`: the required index is written before recursion. Mapping
`subsections` back to the internal `children` tree resolved every proposed
anchor on this document.

## Correction

`FallbackAnchor.children` stays the Python attribute. The provider JSON field
and the fallback prompt use `subsections`. Structure generation identity rolls
so the new prompt and schema are the cache key. No flat tree, truncation, fake
success, or extra adapter.
