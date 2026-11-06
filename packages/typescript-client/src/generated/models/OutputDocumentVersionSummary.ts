/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OutputDocumentVersionSummary = {
    error: string | null;
    ingested_at: string;
    status: 'ingesting' | 'converting' | 'structuring' | 'ready' | 'failed' | 'deleted';
    version_id: string;
    version_no: number;
};

