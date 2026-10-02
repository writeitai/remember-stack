/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputCapabilityReadiness } from './OutputCapabilityReadiness';
import type { OutputVersionPipelineReadiness } from './OutputVersionPipelineReadiness';
export type OutputPipelineReadinessReport = {
    build_revision: string;
    capabilities: Record<string, OutputCapabilityReadiness>;
    document_binding_generation: string | null;
    model_bindings: Record<string, string>;
    ready: boolean;
    versions: Array<OutputVersionPipelineReadiness>;
};

