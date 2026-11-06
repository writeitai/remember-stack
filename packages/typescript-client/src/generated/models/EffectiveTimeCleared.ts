/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What leaving effective time did to one document (D140).
 *
 * Every live declaration of the document is retracted and, when it had
 * declared effective time, the document returns to "the newest processed
 * version is current" for every reader believing after ``cleared_at``.
 */
export type EffectiveTimeCleared = {
    cleared_at: string | null;
    doc_id: string;
    retracted: number;
};

