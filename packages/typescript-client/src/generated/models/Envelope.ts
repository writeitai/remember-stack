/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AggregateReport } from './AggregateReport';
import type { AsOfTemporalScope } from './AsOfTemporalScope';
import type { AtTemporalScope } from './AtTemporalScope';
import type { ChangeRecord } from './ChangeRecord';
import type { ChunkEvidenceResult } from './ChunkEvidenceResult';
import type { CurrentTemporalScope } from './CurrentTemporalScope';
import type { EntityCandidate } from './EntityCandidate';
import type { EvidenceResult } from './EvidenceResult';
import type { EvidenceTotal } from './EvidenceTotal';
import type { FactEvidence } from './FactEvidence';
import type { FactResult } from './FactResult';
import type { Freshness } from './Freshness';
import type { Grain } from './Grain';
import type { GraphEdge } from './GraphEdge';
import type { GraphNode } from './GraphNode';
import type { GraphPath } from './GraphPath';
import type { HistoryTemporalScope } from './HistoryTemporalScope';
import type { Negative } from './Negative';
import type { OverlapTemporalScope } from './OverlapTemporalScope';
import type { PageRef } from './PageRef';
import type { RankedItem } from './RankedItem';
import type { SourceRecord } from './SourceRecord';
import type { TranscriptEntry } from './TranscriptEntry';
import type { Truncation } from './Truncation';
export type Envelope = {
    aggregate?: AggregateReport | null;
    changes?: Array<ChangeRecord>;
    chunks?: Array<ChunkEvidenceResult>;
    dropped_by_hydration?: number;
    edges?: Array<GraphEdge>;
    entities?: Array<EntityCandidate>;
    evidence?: Array<EvidenceResult>;
    evidence_totals?: Array<EvidenceTotal>;
    excluded_unstamped?: number;
    fact_evidence?: Array<FactEvidence>;
    facts?: Array<FactResult>;
    freshness: Freshness;
    grain: Grain;
    negative?: Negative | null;
    nodes?: Array<GraphNode>;
    pages?: Array<PageRef>;
    paths?: Array<GraphPath>;
    ranking?: Array<RankedItem>;
    sources?: Array<SourceRecord>;
    temporal_scope: ((CurrentTemporalScope & {
        mode: 'current';
    }) | (AtTemporalScope & {
        mode: 'at';
    }) | (OverlapTemporalScope & {
        mode: 'overlap';
    }) | (HistoryTemporalScope & {
        mode: 'history';
    }) | (AsOfTemporalScope & {
        mode: 'as_of';
    }));
    transcript?: Array<TranscriptEntry>;
    truncation?: Truncation | null;
};

