/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputAtReadTime } from './OutputAtReadTime';
import type { OutputCurrentReadTime } from './OutputCurrentReadTime';
import type { OutputHistoryReadTime } from './OutputHistoryReadTime';
import type { OutputOverlapReadTime } from './OutputOverlapReadTime';
/**
 * One ``section_history`` call (D140 §6.2).
 */
export type OutputSectionHistoryRequest = {
    cursor: string | null;
    doc_id: string;
    'k': number;
    section_key: string;
    time: (OutputCurrentReadTime | OutputAtReadTime | OutputOverlapReadTime | OutputHistoryReadTime);
};

