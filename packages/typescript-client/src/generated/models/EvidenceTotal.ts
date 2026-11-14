/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type EvidenceTotal = {
    fact_id: string;
    fact_kind: 'relation' | 'observation';
    in_documents_filter_total?: number | null;
    returned: number;
    stance: 'supports' | 'contradicts';
    total: number;
};

