import type { JsonValue } from './JsonValue';
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Caller tags a document must carry (D156 §4.1); every list given must hold.
 *
 * ``all``: the document has every listed tag; ``any``: at least one;
 * ``none``: none of them. A tag is a registered name (matched ignoring case)
 * or a ``tag_id``; an unregistered tag fails the read with ``unknown_tag``
 * before anything is searched. Tags are the document's (lineage's) current
 * tags, also when a time scope reads an older version. An untagged document
 * matches ``none`` and fails ``all`` and ``any``.
 */
export type OutputDocumentTagFilter = {
    all: Array<string>;
    any: Array<string>;
    none: Array<string>;
};
