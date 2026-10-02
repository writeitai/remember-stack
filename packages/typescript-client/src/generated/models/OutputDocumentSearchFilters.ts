import type { JsonValue } from './JsonValue';
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * General document metadata filters (D134 §3); every one given must hold.
 *
 * ``authors`` and ``recipients`` match a person when any listed term equals
 * their normalized address or appears as whole words in their normalized
 * name (lower case, accents removed): ``"alice"`` matches "Alice Novák".
 * Date ranges are inclusive and exclude documents that do not declare the
 * date.
 */
export type OutputDocumentSearchFilters = {
    authors: Array<string>;
    created_from: string | null;
    created_to: string | null;
    doc_ids: Array<string>;
    family: Array<string>;
    language: string | null;
    modified_from: string | null;
    modified_to: string | null;
    recipients: Array<string>;
    thread_ref: string | null;
};
