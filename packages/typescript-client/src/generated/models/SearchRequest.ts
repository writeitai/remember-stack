/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AtReadTime } from './AtReadTime';
import type { CurrentReadTime } from './CurrentReadTime';
import type { DocumentSearchFilters } from './DocumentSearchFilters';
import type { HistoryReadTime } from './HistoryReadTime';
import type { OverlapReadTime } from './OverlapReadTime';
export type SearchRequest = {
    channel?: 'semantic' | 'bm25';
    documents?: DocumentSearchFilters | null;
    'k'?: number;
    query: string;
    time?: (CurrentReadTime | AtReadTime | OverlapReadTime | HistoryReadTime) | null;
};

