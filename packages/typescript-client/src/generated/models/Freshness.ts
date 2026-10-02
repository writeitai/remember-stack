/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { KFreshness } from './KFreshness';
import type { ScopePending } from './ScopePending';
export type Freshness = {
    'k'?: KFreshness | null;
    p1_believed_at_horizon?: string | null;
    p1_written_inline?: boolean;
    pg_live_ts: string;
    scope_pending?: ScopePending | null;
};

