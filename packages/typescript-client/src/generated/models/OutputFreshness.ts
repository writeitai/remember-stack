/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputKFreshness } from './OutputKFreshness';
import type { OutputScopePending } from './OutputScopePending';
export type OutputFreshness = {
    'k': OutputKFreshness | null;
    p1_believed_at_horizon: string | null;
    p1_written_inline: boolean;
    pg_live_ts: string;
    scope_pending: OutputScopePending | null;
};

