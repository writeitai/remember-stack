/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What deleting one document changed in the live memory.
 *
 * The counts describe this call. A call that finishes a deletion another
 * path started reports only the work it finished.
 */
export type OutputDocumentDeletion = {
    claims_retired: number;
    deleted_at: string;
    doc_id: string;
    observations_closed: number;
    relations_closed: number;
};

