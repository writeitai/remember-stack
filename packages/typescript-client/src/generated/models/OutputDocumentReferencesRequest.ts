/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputAtReadTime } from './OutputAtReadTime';
import type { OutputCurrentReadTime } from './OutputCurrentReadTime';
import type { OutputHistoryReadTime } from './OutputHistoryReadTime';
import type { OutputOverlapReadTime } from './OutputOverlapReadTime';
/**
 * One ``document_references`` call (D140 §6.2).
 *
 * Exactly one of ``chunk_id`` (the chunk's version and section are the
 * source) or ``doc_id`` (with an optional ``section_key``). ``time``
 * selects source versions; omitted, it is ``current`` — except with a
 * ``chunk_id``, where it is the chunk version's in-force time up to now.
 */
export type OutputDocumentReferencesRequest = {
    chunk_id: string | null;
    cursor: string | null;
    direction: 'outgoing' | 'incoming' | 'both';
    doc_id: string | null;
    'k': number;
    kinds: Array<'cites' | 'links_to' | 'attaches' | 'replies_to' | 'refers_to' | 'amends' | 'implements'> | null;
    section_key: string | null;
    time: (OutputCurrentReadTime | OutputAtReadTime | OutputOverlapReadTime | OutputHistoryReadTime) | null;
};

