/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputSectionAmendment } from './OutputSectionAmendment';
import type { OutputSectionHistoryRow } from './OutputSectionHistoryRow';
/**
 * A page of ``section_history`` rows.
 *
 * Rows are ordered by effective start for a lineage with declared effective
 * periods (``periodised``) and by ``version_no`` otherwise. ``amendments``
 * is filled on the first page only. ``next_cursor`` pins ``evaluated_at``
 * and ``believed_at`` for the following pages; null exactly at the end.
 */
export type OutputSectionHistoryPage = {
    amendments: Array<OutputSectionAmendment>;
    believed_at: string;
    doc_id: string;
    evaluated_at: string;
    next_cursor: string | null;
    periodised: boolean;
    rows: Array<OutputSectionHistoryRow>;
    section_key: string;
};

