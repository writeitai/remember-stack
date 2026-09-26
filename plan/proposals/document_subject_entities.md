# Proposal: documents as entities in the fact graph

**Status:** not chosen (2026-09-24). **Chosen instead:** D134,
[document metadata and search](../designs/document_metadata_and_search_design.md).

## The idea

Give a document an entity bound one-to-one to its lineage, so claims about
the document ("the workbook Q3_sales_2025.xlsx covers EU revenue") have the
document as their subject in the fact graph, and relations such as "Alice
authored the Q3 report" connect a person to a file.

A full draft was written and reviewed (PR #453, first D134 draft): the
binding lived on `documents.document_entity_id` with a uniqueness
constraint; the entity was minted atomically the first time a claim took the
document as its subject; Claimify cited an engine-supplied `DOCUMENT`
passage and marked such claims; E3 bound only the subject position; two
bound entities never merged; aliases were tracked per contributing document
so hard forget could remove them.

## Why it was not chosen

The questions that motivated it — find a file, find files by author or date,
find information limited to certain files — are answered by general document
metadata, `search_documents` and document filters on `search`, with no
change to entity identity. The entity approach added a new resolution path,
schema, merge guards, alias bookkeeping and forget rules for a benefit
(files as graph nodes) with no demonstrated need.

## Adoption trigger

Adopt when agents repeatedly need **files as nodes in the fact graph** —
graph traversals or relations whose endpoint must be a specific document
("which people authored documents that cite the audit?") — and metadata
filters plus document search demonstrably cannot express those queries.
