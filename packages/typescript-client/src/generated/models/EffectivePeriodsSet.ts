/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DeclaredEffectivePeriod } from './DeclaredEffectivePeriod';
/**
 * What replacing one version's declared periods left in force.
 */
export type EffectivePeriodsSet = {
    declared: number;
    doc_id: string;
    periods: Array<DeclaredEffectivePeriod>;
    retracted: number;
    version_id: string;
};

