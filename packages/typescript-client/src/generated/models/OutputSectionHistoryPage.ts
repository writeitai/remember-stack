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
 * is filled on the first page only. ``cursor`` pins ``evaluated_at`` and
 * ``believed_at`` for the following pages.
 */
export type OutputSectionHistoryPage = {
    amendments: Array<OutputSectionAmendment>;
    believed_at: string;
    cursor: string | null;
    doc_id: string;
    evaluated_at: string;
    periodised: boolean;
    rows: Array<OutputSectionHistoryRow>;
    section_key: string;
};

