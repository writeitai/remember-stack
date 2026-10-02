/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputEffectivePeriodInput } from './OutputEffectivePeriodInput';
/**
 * The complete set of periods one version is in force for (D140).
 *
 * It replaces the version's current declarations: periods not listed are
 * retracted and new ones are declared, atomically. An empty set is allowed
 * and leaves the version with no in-force period; the document keeps its
 * declared effective time.
 */
export type OutputEffectivePeriodsRequest = {
    periods: Array<OutputEffectivePeriodInput>;
};

