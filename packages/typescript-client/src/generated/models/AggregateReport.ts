/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AggregateBucket } from './AggregateBucket';
export type AggregateReport = {
    bounded_by?: string | null;
    buckets?: Array<AggregateBucket>;
    form: string;
    total: number;
};

