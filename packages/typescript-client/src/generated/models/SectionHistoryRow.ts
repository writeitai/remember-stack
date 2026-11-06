/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EffectiveInterval } from './EffectiveInterval';
import type { SectionHistorySection } from './SectionHistorySection';
/**
 * One selected version of the lineage and what it holds under the key.
 *
 * ``status``: ``present`` (``section`` is set), ``absent`` (the version is
 * indexed and lacks the key — a removed section), ``not_indexed`` (its
 * sections predate section keys and are not backfilled yet, so absence
 * cannot be told) or ``processing`` (the version is not readable yet).
 */
export type SectionHistoryRow = {
    effective?: Array<EffectiveInterval>;
    section?: SectionHistorySection | null;
    status: 'present' | 'absent' | 'not_indexed' | 'processing';
    version_id: string;
    version_key?: string | null;
    version_no: number;
};

