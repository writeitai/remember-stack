import type { JsonValue } from './JsonValue';
/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputGraphInvocation } from './OutputGraphInvocation';
import type { OutputQueryErrorCode } from './OutputQueryErrorCode';
import type { OutputResultColumn } from './OutputResultColumn';
import type { OutputResultLimits } from './OutputResultLimits';
import type { OutputSemanticInvocation } from './OutputSemanticInvocation';
/**
 * One complete `QueryResult/v1` response.
 */
export type OutputQueryResult = {
    columns: Array<OutputResultColumn>;
    contract: 'QueryResult/v1';
    deployment_id: string;
    elapsed_ms: number;
    empty_result: boolean;
    error_code: OutputQueryErrorCode | null;
    error_message: string | null;
    evaluated_at: string | null;
    exact_total: number | null;
    exact_total_known: boolean;
    execution_started_at: string;
    grade: 'exploratory_tabular';
    graph_invocations: Array<OutputGraphInvocation>;
    limits: OutputResultLimits;
    negative_kind: null;
    ordered_result: boolean;
    pg_snapshot_at: string | null;
    query_hash: string;
    query_language: 'sql';
    query_space_schema: 'memory_v1';
    referenced_functions: Array<string>;
    referenced_views: Array<string>;
    request_id: string;
    returned_byte_count: number;
    returned_row_count: number;
    rows: Array<Array<JsonValue>>;
    saved_query: Record<string, string> | null;
    semantic_invocations: Array<OutputSemanticInvocation>;
    source_grain_tags: Array<string>;
    surface_manifest_hash: string;
    termination_reason: 'completed' | 'rejected' | 'failed';
    truncated: boolean;
    truncation_reason: string | null;
    warnings: Array<string>;
};
