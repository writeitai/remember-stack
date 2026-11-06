import type { JsonValue } from './JsonValue';
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ValidationError = {
    ctx?: Record<string, JsonValue>;
    input?: JsonValue;
    loc: Array<(string | number)>;
    msg: string;
    type: string;
};
