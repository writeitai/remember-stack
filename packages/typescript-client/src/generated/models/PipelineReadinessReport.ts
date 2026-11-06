/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CapabilityReadiness } from './CapabilityReadiness';
import type { VersionPipelineReadiness } from './VersionPipelineReadiness';
export type PipelineReadinessReport = {
    build_revision?: string;
    capabilities: Record<string, CapabilityReadiness>;
    document_binding_generation?: string | null;
    model_bindings?: Record<string, string>;
    ready: boolean;
    versions: Array<VersionPipelineReadiness>;
};

