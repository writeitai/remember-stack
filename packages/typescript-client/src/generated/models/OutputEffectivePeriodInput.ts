/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One declared period during which a version's text is in force (D140).
 *
 * The period is half-open: ``effective_from`` is included and
 * ``effective_until`` is not. Without ``effective_until`` the period lasts
 * until the next declared start in the document's lineage, so declaring a
 * new edition ends its predecessor without touching it.
 */
export type OutputEffectivePeriodInput = {
    effective_from: string;
    effective_until: string | null;
};

