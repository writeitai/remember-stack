/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputDocumentVersionSummary } from './OutputDocumentVersionSummary';
export type OutputDocumentSummary = {
    doc_id: string;
    first_seen_at: string;
    latest: OutputDocumentVersionSummary;
    serving: boolean;
    source_kind: string;
    source_uri: string | null;
    title: string | null;
};

