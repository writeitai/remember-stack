/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputClaimValidPrecision } from './OutputClaimValidPrecision';
import type { OutputFactSupport } from './OutputFactSupport';
export type OutputGraphEdge = {
    evidence_count: number;
    fact: string | null;
    ingested_at: string | null;
    invalidated_at: string | null;
    object_id: string;
    predicate: string;
    relation_id: string;
    subject_id: string;
    support: OutputFactSupport;
    valid_from: string | null;
    valid_precision: OutputClaimValidPrecision;
    valid_until: string | null;
};

