/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EvidenceSpan } from './EvidenceSpan';
export type EvidenceResult = {
    asserted_at?: string | null;
    char_end: number;
    char_start: number;
    chunk_id: string;
    claim_id: string;
    claim_text: string;
    claim_valid_from?: string | null;
    claim_valid_kind?: string | null;
    claim_valid_precision?: string;
    claim_valid_until?: string | null;
    corroboration_count?: number | null;
    doc_id: string;
    document_title?: string | null;
    evidence_spans?: Array<EvidenceSpan>;
    grouped_claim_ids?: Array<string>;
    is_attributed: boolean;
    is_current_testimony: boolean;
    source_kind?: string | null;
    source_span: string;
};

