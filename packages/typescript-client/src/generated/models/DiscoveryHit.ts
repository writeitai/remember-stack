/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Exact wire contract for one query-space search result.
 */
export type DiscoveryHit = {
    kind: 'view' | 'function' | 'core_operation' | 'example';
    name: string;
    purpose: string;
    score: number;
    tags: Array<string>;
};

