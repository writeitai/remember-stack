/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PipelineStageReadiness } from './PipelineStageReadiness';
export type VersionPipelineReadiness = {
    ready: boolean;
    stages: Array<PipelineStageReadiness>;
    version_id: string;
};

