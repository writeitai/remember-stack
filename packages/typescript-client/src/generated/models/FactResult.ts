/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Contradiction } from './Contradiction';
import type { FactSupport } from './FactSupport';
import type { TemporalMatch } from './TemporalMatch';
import type { Validity } from './Validity';
export type FactResult = {
    contradiction?: Contradiction | null;
    contradiction_group?: string | null;
    evidence_count: number;
    fact_id: string;
    kind: string;
    label: string;
    support?: FactSupport;
    temporal_match?: TemporalMatch;
    validity: Validity;
};

