/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JsonValue } from './JsonValue';
export type ConnectorCreate = {
    configuration?: Record<string, JsonValue>;
    credential_ref?: string | null;
    kind: string;
    name: string;
};

