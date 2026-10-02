/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputAggregateReport } from './OutputAggregateReport';
import type { OutputAsOfTemporalScope } from './OutputAsOfTemporalScope';
import type { OutputAtTemporalScope } from './OutputAtTemporalScope';
import type { OutputChangeRecord } from './OutputChangeRecord';
import type { OutputChunkEvidenceResult } from './OutputChunkEvidenceResult';
import type { OutputCurrentTemporalScope } from './OutputCurrentTemporalScope';
import type { OutputEntityCandidate } from './OutputEntityCandidate';
import type { OutputEvidenceResult } from './OutputEvidenceResult';
import type { OutputEvidenceTotal } from './OutputEvidenceTotal';
import type { OutputFactEvidence } from './OutputFactEvidence';
import type { OutputFactResult } from './OutputFactResult';
import type { OutputFreshness } from './OutputFreshness';
import type { OutputGrain } from './OutputGrain';
import type { OutputGraphEdge } from './OutputGraphEdge';
import type { OutputGraphNode } from './OutputGraphNode';
import type { OutputGraphPath } from './OutputGraphPath';
import type { OutputHistoryTemporalScope } from './OutputHistoryTemporalScope';
import type { OutputNegative } from './OutputNegative';
import type { OutputOverlapTemporalScope } from './OutputOverlapTemporalScope';
import type { OutputPageRef } from './OutputPageRef';
import type { OutputRankedItem } from './OutputRankedItem';
import type { OutputSourceRecord } from './OutputSourceRecord';
import type { OutputTranscriptEntry } from './OutputTranscriptEntry';
import type { OutputTruncation } from './OutputTruncation';
export type OutputEnvelope = {
    aggregate: OutputAggregateReport | null;
    changes: Array<OutputChangeRecord>;
    chunks: Array<OutputChunkEvidenceResult>;
    dropped_by_hydration: number;
    edges: Array<OutputGraphEdge>;
    entities: Array<OutputEntityCandidate>;
    evidence: Array<OutputEvidenceResult>;
    evidence_totals: Array<OutputEvidenceTotal>;
    excluded_unstamped: number;
    fact_evidence: Array<OutputFactEvidence>;
    facts: Array<OutputFactResult>;
    freshness: OutputFreshness;
    grain: OutputGrain;
    negative: OutputNegative | null;
    nodes: Array<OutputGraphNode>;
    pages: Array<OutputPageRef>;
    paths: Array<OutputGraphPath>;
    ranking: Array<OutputRankedItem>;
    sources: Array<OutputSourceRecord>;
    temporal_scope: ((OutputCurrentTemporalScope & {
        mode: 'current';
    }) | (OutputAtTemporalScope & {
        mode: 'at';
    }) | (OutputOverlapTemporalScope & {
        mode: 'overlap';
    }) | (OutputHistoryTemporalScope & {
        mode: 'history';
    }) | (OutputAsOfTemporalScope & {
        mode: 'as_of';
    }));
    transcript: Array<OutputTranscriptEntry>;
    truncation: OutputTruncation | null;
};

