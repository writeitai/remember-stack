/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputDocumentSearchFilters } from './OutputDocumentSearchFilters';
export type OutputSearchRequest = {
    channel: 'semantic' | 'bm25';
    documents: OutputDocumentSearchFilters | null;
    'k': number;
    query: string;
};

