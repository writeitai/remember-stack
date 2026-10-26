/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ClaimValidPrecision } from './ClaimValidPrecision';
import type { FactSupport } from './FactSupport';
/**
 * One traversed relation; on a citation path, a document reference.
 *
 * ``bindings`` and ``source_labels`` are the distinct, sorted ``binding`` and
 * non-null ``source_label`` values of the references a citation edge stands
 * for. Both are empty on entity edges.
 */
export type GraphEdge = {
    bindings?: Array<'floating' | 'pinned'>;
    evidence_count: number;
    fact: string | null;
    ingested_at: string | null;
    invalidated_at: string | null;
    object_id: string;
    predicate: string;
    relation_id: string;
    source_labels?: Array<string>;
    subject_id: string;
    support?: FactSupport;
    valid_from: string | null;
    valid_precision?: ClaimValidPrecision;
    valid_until: string | null;
};

