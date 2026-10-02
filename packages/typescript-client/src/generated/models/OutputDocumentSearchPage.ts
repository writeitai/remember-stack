/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputDocumentPeopleMatch } from './OutputDocumentPeopleMatch';
import type { OutputDocumentSearchResult } from './OutputDocumentSearchResult';
import type { OutputScopePending } from './OutputScopePending';
/**
 * A page of ``search_documents`` results.
 */
export type OutputDocumentSearchPage = {
    as_of: string;
    cursor: string | null;
    documents: Array<OutputDocumentSearchResult>;
    people_matched: Array<OutputDocumentPeopleMatch>;
    scope_pending: OutputScopePending | null;
};

