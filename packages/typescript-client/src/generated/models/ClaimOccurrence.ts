/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EffectiveInterval } from './EffectiveInterval';
import type { EvidenceSpan } from './EvidenceSpan';
import type { JsonValue } from './JsonValue';
/**
 * Where a claim occurs in one selected version (D140 §3.4).
 */
export type ClaimOccurrence = {
    char_end: number;
    char_start: number;
    chunk_id: string;
    effective?: Array<EffectiveInterval>;
    evidence_spans?: Array<EvidenceSpan>;
    representation_id: string;
    served_version?: boolean;
    source_locators?: JsonValue;
    version_id: string;
};

