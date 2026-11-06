/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Bounded shortest entity-path request.
 */
export type GraphPathRequest = {
    believed_at?: string | null;
    from_entity_id: string;
    max_hops?: number;
    predicates?: Array<string>;
    to_entity_id: string;
    valid_at?: string | null;
};

