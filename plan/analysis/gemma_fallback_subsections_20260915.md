# Gemma fallback wire field: subsections

Live conv-42 D1 fallback generation on Gemma/Vertex completed when the recursive
JSON field was named `subsections` instead of `children`. This note records that
measurement. It does not change the D79 anchor-tree contract.

## What failed

The fallback schema requires `anchor`, `occurrence_index`, and a nested list on
every node. Vertex's constrained decoder emitted keys alphabetically:
`anchor`, `children`, `occurrence_index`. After closing the nested list it
padded whitespace instead of writing the remaining required index. The JSON
never closed. `propertyOrdering` did not change the order.

A one-field structured call on the same route succeeded. Retrying the unchanged
root-summary schema also succeeded. The hang is this field name, not the
provider route or a missing cutoff.

## What worked

Renaming only that nested field to `subsections` completed in 6.3s with valid
JSON. Alphabetical emission then becomes `anchor`, `occurrence_index`,
`subsections`: the required index is written before recursion. Mapping
`subsections` back to the internal `children` tree resolved every proposed
anchor.

## Correction

`FallbackAnchor.children` stays the Python attribute. The provider JSON field
and the fallback prompt use `subsections`. Structure generation identity rolls
so the new prompt and schema are the cache key. No flat tree, truncation, fake
success, or extra adapter.
