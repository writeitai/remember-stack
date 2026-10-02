/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One §3.4 nomination invocation's disclosure (populated by Batch C).
 */
export type OutputSemanticInvocation = {
    confirmed: number;
    dropped_absent: number;
    dropped_absent_current: number;
    dropped_absent_projection: number;
    dropped_ambiguous: number;
    dropped_body_mismatch: number;
    dropped_filtered: number;
    dropped_hash_mismatch: number;
    dropped_stale: number;
    embedder_generation: string | null;
    function: string;
    generation: string | null;
    nominated: number;
    pg_confirmed_at: string | null;
    policy_generation: string | null;
    termination_reason: string | null;
};

