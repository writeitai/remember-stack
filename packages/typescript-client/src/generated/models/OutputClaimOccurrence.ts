/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputEffectiveInterval } from './OutputEffectiveInterval';
import type { OutputEvidenceSpan } from './OutputEvidenceSpan';
import type { OutputJsonValue } from './OutputJsonValue';
/**
 * Where a claim occurs in one selected version (D140 §3.4).
 */
export type OutputClaimOccurrence = {
    char_end: number;
    char_start: number;
    chunk_id: string;
    effective: Array<OutputEffectiveInterval>;
    evidence_spans: Array<OutputEvidenceSpan>;
    representation_id: string;
    served_version: boolean;
    source_locators: OutputJsonValue;
    version_id: string;
};

