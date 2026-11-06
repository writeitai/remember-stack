/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputContradiction } from './OutputContradiction';
import type { OutputFactSupport } from './OutputFactSupport';
import type { OutputTemporalMatch } from './OutputTemporalMatch';
import type { OutputValidity } from './OutputValidity';
export type OutputFactResult = {
    contradiction: OutputContradiction | null;
    contradiction_group: string | null;
    evidence_count: number;
    fact_id: string;
    kind: string;
    label: string;
    support: OutputFactSupport;
    temporal_match: OutputTemporalMatch;
    validity: OutputValidity;
};

