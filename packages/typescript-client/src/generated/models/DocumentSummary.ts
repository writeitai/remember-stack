/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DocumentVersionSummary } from './DocumentVersionSummary';
export type DocumentSummary = {
    doc_id: string;
    first_seen_at: string;
    latest: DocumentVersionSummary;
    serving: boolean;
    source_kind: string;
    source_uri?: string | null;
    title?: string | null;
};

