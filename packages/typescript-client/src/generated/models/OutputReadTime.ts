/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputAtReadTime } from './OutputAtReadTime';
import type { OutputCurrentReadTime } from './OutputCurrentReadTime';
import type { OutputHistoryReadTime } from './OutputHistoryReadTime';
import type { OutputOverlapReadTime } from './OutputOverlapReadTime';
export type OutputReadTime = ((OutputCurrentReadTime & {
    mode: 'current';
}) | (OutputAtReadTime & {
    mode: 'at';
}) | (OutputOverlapReadTime & {
    mode: 'overlap';
}) | (OutputHistoryReadTime & {
    mode: 'history';
}));

