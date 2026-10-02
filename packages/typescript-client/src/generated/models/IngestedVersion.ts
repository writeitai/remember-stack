/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type IngestedVersion = {
    content_hash: string;
    created: boolean;
    deployment_id: string;
    doc_id: string;
    mime?: string | null;
    parked?: 'no_route' | null;
    processing_admission?: 'not_required' | 'pending';
    title?: string | null;
    version_id: string;
    versioning_mode?: 'snapshot' | 'living' | null;
};

