/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputDeclaredEffectivePeriod } from './OutputDeclaredEffectivePeriod';
/**
 * What replacing one version's declared periods left in force.
 */
export type OutputEffectivePeriodsSet = {
    declared: number;
    doc_id: string;
    periods: Array<OutputDeclaredEffectivePeriod>;
    retracted: number;
    version_id: string;
};

