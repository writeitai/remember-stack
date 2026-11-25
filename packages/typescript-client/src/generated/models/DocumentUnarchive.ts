/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What unarchiving one document restored (D159 §2.3).
 *
 * Unarchive is the exact undo of the archive: the testimony it ended is
 * current again and the facts it closed that have support again are
 * reopened. Unarchiving a live document answers zero counts.
 */
export type DocumentUnarchive = {
    claims_restored: number;
    doc_id: string;
    facts_reopened: number;
};

