/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EffectiveInterval } from './EffectiveInterval';
import type { ReferenceWindow } from './ReferenceWindow';
/**
 * The resolved target side of one reference row.
 *
 * Only what the row's status allows is set: the lineage for
 * ``target_not_in_force`` and ``pinned_version_unavailable``; the version
 * and ``applies_during`` for ``target_processing``; the section and its
 * first chunks for ``resolved``.
 */
export type DocumentReferenceTarget = {
    applies_during?: ReferenceWindow | null;
    concurrent?: boolean;
    doc_id: string;
    effective?: Array<EffectiveInterval>;
    first_chunk_ids?: Array<string>;
    representation_id?: string | null;
    section_key?: string | null;
    section_title?: string | null;
    version_id?: string | null;
    version_key?: string | null;
};

