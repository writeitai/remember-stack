/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DocumentSearchFilters } from './DocumentSearchFilters';
export type SearchRequest = {
    channel?: 'semantic' | 'bm25';
    documents?: DocumentSearchFilters | null;
    'k'?: number;
    query: string;
};

