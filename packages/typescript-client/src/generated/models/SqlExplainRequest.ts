import type { JsonValue } from './JsonValue';
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for `POST /query/sql/explain` — sql and parameters only.
 */
export type SqlExplainRequest = {
    parameters?: Array<JsonValue>;
    sql: string;
};
