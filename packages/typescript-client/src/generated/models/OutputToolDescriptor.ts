import type { JsonValue } from './JsonValue';
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OutputToolDescriptor = {
    answer_intent: string;
    description: string;
    implementation_plan_hash: string | null;
    input_schema: Record<string, JsonValue>;
    mutates: boolean | null;
    name: string;
    output_grain: string | null;
    result_contract: string;
    result_schema: Record<string, JsonValue>;
    version: number | null;
};
