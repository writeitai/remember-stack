/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputAtReadTime } from './OutputAtReadTime';
import type { OutputCurrentReadTime } from './OutputCurrentReadTime';
import type { OutputDocumentSearchFilters } from './OutputDocumentSearchFilters';
import type { OutputHistoryReadTime } from './OutputHistoryReadTime';
import type { OutputOverlapReadTime } from './OutputOverlapReadTime';
export type OutputSearchRequest = {
    channel: 'semantic' | 'bm25';
    documents: OutputDocumentSearchFilters | null;
    'k': number;
    query: string;
    time: (OutputCurrentReadTime | OutputAtReadTime | OutputOverlapReadTime | OutputHistoryReadTime) | null;
};

