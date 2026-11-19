/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AtReadTime } from './AtReadTime';
import type { CurrentReadTime } from './CurrentReadTime';
import type { DocumentSearchFilters } from './DocumentSearchFilters';
import type { HistoryReadTime } from './HistoryReadTime';
import type { OverlapReadTime } from './OverlapReadTime';
/**
 * One ``search_documents`` call.
 *
 * With a ``query`` the results are ranked by name and content matches (top
 * ``k``); there is no cursor. Without a query they are every document the
 * filters match, newest judged-version ingest time first, paged by
 * ``limit`` and ``cursor`` (D154).
 */
export type DocumentSearchRequest = {
    cursor?: string | null;
    filters?: DocumentSearchFilters;
    'k'?: number;
    limit?: number;
    query?: string | null;
    time?: ((CurrentReadTime & {
        mode: 'current';
    }) | (AtReadTime & {
        mode: 'at';
    }) | (OverlapReadTime & {
        mode: 'overlap';
    }) | (HistoryReadTime & {
        mode: 'history';
    })) | null;
    versions?: 'current' | 'all';
};

