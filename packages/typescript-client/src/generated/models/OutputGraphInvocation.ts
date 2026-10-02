/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One graph helper's terminal work and truncation disclosure.
 */
export type OutputGraphInvocation = {
    applied_believed_at: string | null;
    applied_valid_at: string | null;
    effective_depth: number;
    effective_expansion_budget: number;
    effective_frontier_budget: number;
    effective_result_budget: number;
    effective_time_budget_ms: number;
    evaluated_at: string | null;
    examined_edges: number;
    function: 'graph_neighborhood' | 'graph_path' | 'graph_citation_path';
    ordinal: number;
    returned_paths: number;
    truncated: boolean;
    truncation_reason: 'depth_budget' | 'expansion_budget' | 'frontier_budget' | 'result_budget' | 'time_budget' | null;
};

