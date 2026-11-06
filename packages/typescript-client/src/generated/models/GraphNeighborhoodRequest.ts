/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Bounded entity-neighborhood request.
 */
export type GraphNeighborhoodRequest = {
    believed_at?: string | null;
    continuation?: string | null;
    entity_id: string;
    hops?: number;
    include_paths?: boolean;
    limit?: number;
    predicates?: Array<string>;
    valid_at?: string | null;
};

