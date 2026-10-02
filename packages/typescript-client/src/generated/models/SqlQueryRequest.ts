/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for `POST /query/sql` (execution fields allowed).
 */
export type SqlQueryRequest = {
    max_rows?: number | null;
    parameters?: Array<any>;
    sql: string;
};

