/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ReadinessRequirements } from './ReadinessRequirements';
/**
 * Exhaustive readiness capabilities for a bounded version set.
 */
export type PipelineReadinessRequest = {
    require: ReadinessRequirements;
    version_ids: Array<string>;
};

