/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AtReadTime } from './AtReadTime';
import type { CurrentReadTime } from './CurrentReadTime';
import type { HistoryReadTime } from './HistoryReadTime';
import type { OverlapReadTime } from './OverlapReadTime';
/**
 * One ``section_history`` call (D140 §6.2).
 */
export type SectionHistoryRequest = {
    cursor?: string | null;
    doc_id: string;
    'k'?: number;
    section_key: string;
    time?: ((CurrentReadTime & {
        mode: 'current';
    }) | (AtReadTime & {
        mode: 'at';
    }) | (OverlapReadTime & {
        mode: 'overlap';
    }) | (HistoryReadTime & {
        mode: 'history';
    }));
};

