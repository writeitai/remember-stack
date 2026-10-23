/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What ``GET /processing-layers`` reports for the two switchable layers.
 *
 * ``backfilling`` means the layer is enabled but some versions processed
 * while it was off have not been processed by it yet.
 */
export type OutputProcessingLayerStates = {
    claims: 'enabled' | 'backfilling' | 'disabled';
    facts: 'enabled' | 'backfilling' | 'disabled';
};

