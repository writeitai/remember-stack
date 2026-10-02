/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ClaimValidPrecision } from './ClaimValidPrecision';
import type { FactSupport } from './FactSupport';
export type GraphEdge = {
    evidence_count: number;
    fact: string | null;
    ingested_at: string | null;
    invalidated_at: string | null;
    object_id: string;
    predicate: string;
    relation_id: string;
    subject_id: string;
    support?: FactSupport;
    valid_from: string | null;
    valid_precision?: ClaimValidPrecision;
    valid_until: string | null;
};

