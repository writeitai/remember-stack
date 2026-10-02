/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PipelineStageReadiness = {
    component_version: string;
    defer_reason?: 'scheduled' | 'retry_backoff' | 'budget' | 'no_route' | null;
    finished_at?: string | null;
    stage: string;
    status: 'missing' | 'pending' | 'running' | 'succeeded' | 'failed' | 'dead_letter' | 'skipped';
};

