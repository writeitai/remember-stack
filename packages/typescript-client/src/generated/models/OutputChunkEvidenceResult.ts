/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputEffectiveInterval } from './OutputEffectiveInterval';
export type OutputChunkEvidenceResult = {
    char_end: number;
    char_start: number;
    chunk_id: string;
    chunk_text: string;
    context_prefix: string | null;
    doc_id: string;
    document_title: string | null;
    effective: Array<OutputEffectiveInterval>;
    published_at: string | null;
    representation_id: string;
    section_role: string | null;
    served_version: boolean;
    source_kind: string;
    source_modified_at: string | null;
    version_id: string;
};

