/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputClaimValidPrecision } from './OutputClaimValidPrecision';
export type OutputValidity = {
    ingested_at: string;
    invalidated_at: string | null;
    valid_from: string | null;
    valid_precision: OutputClaimValidPrecision;
    valid_until: string | null;
};

