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
 * With a ``query`` the results are ranked by name and content matches and
 * there is no cursor. Without one they are every document the filters
 * match, newest declared creation date first, paged by ``cursor``.
 */
export type DocumentSearchRequest = {
    cursor?: string | null;
    filters?: DocumentSearchFilters;
    'k'?: number;
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

