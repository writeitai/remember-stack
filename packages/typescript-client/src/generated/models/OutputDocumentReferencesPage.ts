/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputDocumentReference } from './OutputDocumentReference';
import type { OutputDocumentReferencesTooBroad } from './OutputDocumentReferencesTooBroad';
/**
 * A page of ``document_references`` rows.
 *
 * Ordered by direction (outgoing first), source document, source version,
 * reference, window start and target version. ``cursor`` pins
 * ``evaluated_at`` and ``believed_at``; a page may be short when the scan
 * bound was reached and still carry a cursor. ``too_broad`` is set (and
 * ``rows`` empty) when the section has too many descendant keys.
 */
export type OutputDocumentReferencesPage = {
    believed_at: string;
    cursor: string | null;
    evaluated_at: string;
    rows: Array<OutputDocumentReference>;
    too_broad: OutputDocumentReferencesTooBroad | null;
};

