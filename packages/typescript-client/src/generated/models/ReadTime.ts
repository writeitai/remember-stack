/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AtReadTime } from './AtReadTime';
import type { CurrentReadTime } from './CurrentReadTime';
import type { HistoryReadTime } from './HistoryReadTime';
import type { OverlapReadTime } from './OverlapReadTime';
export type ReadTime = ((CurrentReadTime & {
    mode: 'current';
}) | (AtReadTime & {
    mode: 'at';
}) | (OverlapReadTime & {
    mode: 'overlap';
}) | (HistoryReadTime & {
    mode: 'history';
}));

