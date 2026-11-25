/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One forget record (D159 §3.5): IDs and progress, never content.
 *
 * From the request on, the document is gone from every read; ``status``
 * says how far erasing what was derived from it has come.
 * ``blocking_k_paths`` lists the authored knowledge pages whose owner must
 * remove their citation of the document before the forget completes, and
 * ``last_error`` the code of the last failed sweep, which is retried.
 */
export type OutputForget = {
    blocking_k_paths: Array<string>;
    completed_at: string | null;
    doc_id: string;
    forget_id: string;
    last_error: string | null;
    requested_at: string;
    status: 'erasing' | 'waiting_for_redaction' | 'complete';
};

