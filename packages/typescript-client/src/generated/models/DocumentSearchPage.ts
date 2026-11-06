/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DocumentPeopleMatch } from './DocumentPeopleMatch';
import type { DocumentSearchResult } from './DocumentSearchResult';
import type { ScopePending } from './ScopePending';
/**
 * A page of ``search_documents`` results.
 */
export type DocumentSearchPage = {
    as_of: string;
    cursor?: string | null;
    documents: Array<DocumentSearchResult>;
    people_matched?: Array<DocumentPeopleMatch>;
    scope_pending?: ScopePending | null;
};

