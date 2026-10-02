import type { JsonValue } from './JsonValue';
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for `POST /query/saved/{namespace}/{name}/run`.
 */
export type RunSavedQueryRequest = {
    max_rows?: number | null;
    parameters?: Array<JsonValue>;
    version?: number | null;
};
