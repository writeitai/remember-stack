/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Lineages whose in-force text for a time scope is still processing (D140 §3.7).
 *
 * A scoped read that returns nothing from a lineage may mean "nothing is in
 * force" or "the version in force is not readable yet"; this block names the
 * second case for the lineages the request touched.
 */
export type rememberstack__model__envelope__ScopePending = {
    count: number;
    doc_ids: Array<string>;
};

