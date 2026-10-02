/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputPipelineStageReadiness } from './OutputPipelineStageReadiness';
export type OutputVersionPipelineReadiness = {
    ready: boolean;
    stages: Array<OutputPipelineStageReadiness>;
    version_id: string;
};

