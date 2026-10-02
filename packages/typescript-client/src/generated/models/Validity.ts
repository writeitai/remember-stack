/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ClaimValidPrecision } from './ClaimValidPrecision';
export type Validity = {
    ingested_at: string;
    invalidated_at: string | null;
    valid_from: string | null;
    valid_precision?: ClaimValidPrecision;
    valid_until: string | null;
};

