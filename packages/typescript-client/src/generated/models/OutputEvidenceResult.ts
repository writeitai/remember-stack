/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputClaimOccurrence } from './OutputClaimOccurrence';
import type { OutputEffectiveInterval } from './OutputEffectiveInterval';
import type { OutputEvidenceSpan } from './OutputEvidenceSpan';
export type OutputEvidenceResult = {
    asserted_at: string | null;
    char_end: number;
    char_start: number;
    chunk_id: string;
    claim_id: string;
    claim_text: string;
    claim_valid_from: string | null;
    claim_valid_kind: string | null;
    claim_valid_precision: string;
    claim_valid_until: string | null;
    corroboration_count: number | null;
    doc_id: string;
    document_title: string | null;
    effective: Array<OutputEffectiveInterval>;
    evidence_spans: Array<OutputEvidenceSpan>;
    grouped_claim_ids: Array<string>;
    is_attributed: boolean;
    is_current_testimony: boolean;
    occurrences: Array<OutputClaimOccurrence>;
    representation_id: string | null;
    source_kind: string | null;
    source_span: string;
    version_id: string | null;
};

