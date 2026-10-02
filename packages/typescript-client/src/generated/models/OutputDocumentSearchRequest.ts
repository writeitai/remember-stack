/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputDocumentSearchFilters } from './OutputDocumentSearchFilters';
/**
 * One ``search_documents`` call.
 *
 * With a ``query`` the results are ranked by name and content matches and
 * there is no cursor. Without one they are every document the filters
 * match, newest declared creation date first, paged by ``cursor``.
 */
export type OutputDocumentSearchRequest = {
    cursor: string | null;
    filters: OutputDocumentSearchFilters;
    'k': number;
    query: string | null;
    versions: 'current' | 'all';
};

