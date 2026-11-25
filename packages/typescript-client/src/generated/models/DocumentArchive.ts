/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What archiving one document changed in the live memory (D159 §2.1).
 *
 * The document stays readable as history. The counts describe this call: a
 * repeat answers the same ``archived_at`` with zero counts, and a call that
 * finishes an archive another path started reports only what it finished.
 */
export type DocumentArchive = {
    archived_at: string;
    claims_retired: number;
    doc_id: string;
    observations_closed: number;
    relations_closed: number;
};

