/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type CapabilityReadiness = {
    /**
     * Null for p3: the live corpus has no global build time.
     */
    built_at?: string | null;
    checked_at: string;
    /**
     * Null for p3: the live corpus has no global publication time.
     */
    published_at?: string | null;
    ready: boolean;
    reason: string;
    required: boolean;
    /**
     * For p3, the export digest of one ready requested version; null for multiple versions or incomplete publication.
     */
    version?: string | null;
};

