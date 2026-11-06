/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ReferenceItemError } from './ReferenceItemError';
/**
 * One production of a version's references (D140 §6.1).
 *
 * Rows of a generation are visible only while it is ``active``; a version
 * has at most one active generation per origin. A supplied generation is
 * ``pending`` until the E0 crossref worker validates and activates it,
 * ``rejected`` (with ``errors``) when it names an unknown source section,
 * and ``superseded`` once a later PUT or generation replaced it.
 */
export type ReferenceGeneration = {
    activated_at?: string | null;
    created_at: string;
    crossref_version?: string | null;
    doc_id: string;
    errors?: Array<ReferenceItemError>;
    generation_id: string;
    input_hash: string;
    item_count?: number | null;
    origin: 'supplied' | 'extracted';
    representation_id?: string | null;
    request_seq?: number | null;
    status: 'pending' | 'active' | 'rejected' | 'superseded';
    version_id: string;
};

