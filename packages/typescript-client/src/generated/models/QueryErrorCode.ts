/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Every public failure code, exactly as bound in design §4.1.
 */
export type QueryErrorCode = 'parse_error' | 'multiple_statements' | 'statement_not_allowed' | 'relation_not_allowed' | 'function_not_allowed' | 'function_placement_not_allowed' | 'operator_not_allowed' | 'invalid_parameter' | 'schema_version_mismatch' | 'unbounded_recursion' | 'quota_exceeded' | 'concurrency_exceeded' | 'saved_query_not_found' | 'saved_query_disabled' | 'saved_query_incompatible' | 'saved_query_revalidation_pending' | 'statement_timeout' | 'lock_timeout' | 'cancelled' | 'resource_limit' | 'execution_error' | 'pg_unavailable' | 'p1_unavailable' | 'graph_unavailable' | 'corpus_body_unavailable' | 'generation_unavailable' | 'confirmation_failed';
