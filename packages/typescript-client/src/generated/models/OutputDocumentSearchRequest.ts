/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputAtReadTime } from './OutputAtReadTime';
import type { OutputCurrentReadTime } from './OutputCurrentReadTime';
import type { OutputDocumentSearchFilters } from './OutputDocumentSearchFilters';
import type { OutputHistoryReadTime } from './OutputHistoryReadTime';
import type { OutputOverlapReadTime } from './OutputOverlapReadTime';
/**
 * One ``search_documents`` call.
 *
 * With a ``query`` the results are ranked by name and content matches (top
 * ``k``); there is no cursor. Without a query they are every document the
 * filters match, newest judged-version ingest time first, paged by
 * ``limit`` and ``cursor`` (D154).
 */
export type OutputDocumentSearchRequest = {
    cursor: string | null;
    filters: OutputDocumentSearchFilters;
    'k': number;
    limit: number;
    query: string | null;
    time: ((OutputCurrentReadTime & {
        mode: 'current';
    }) | (OutputAtReadTime & {
        mode: 'at';
    }) | (OutputOverlapReadTime & {
        mode: 'overlap';
    }) | (OutputHistoryReadTime & {
        mode: 'history';
    })) | null;
    versions: 'current' | 'all';
};

