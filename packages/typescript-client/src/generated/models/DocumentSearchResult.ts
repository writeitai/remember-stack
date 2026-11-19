/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DocumentSearchPerson } from './DocumentSearchPerson';
import type { EffectiveInterval } from './EffectiveInterval';
import type { JsonValue } from './JsonValue';
import type { MatchingEdition } from './MatchingEdition';
/**
 * One matching document, described by the version it was judged by.
 */
export type DocumentSearchResult = {
    authors?: Array<DocumentSearchPerson>;
    created_at?: string | null;
    doc_id: string;
    effective?: Array<EffectiveInterval>;
    extra?: Record<string, JsonValue>;
    family: string;
    file_name?: string | null;
    language?: string | null;
    lineage_title?: string | null;
    matched_by?: Array<'name' | 'content'>;
    matching_editions?: Array<MatchingEdition>;
    modified_at?: string | null;
    other_matching_version_ids?: Array<string>;
    overview?: string | null;
    /**
     * Relative corpus folder only for the served selected version with a matching nullable publication marker. Null for historical, raw-only, unpublished, updating or failed publications. Type/topic/title changes move it: re-resolve by doc_id; only the folder suffix is stable. Retain version_id and representation_id for exact source identity.
     */
    p3_path?: string | null;
    recipients?: Array<DocumentSearchPerson>;
    representation_id?: string | null;
    score?: number | null;
    /**
     * Whether version_id is served, independently of corpus publication.
     */
    served_version?: boolean;
    source_path?: string | null;
    status: 'ingesting' | 'converting' | 'structuring' | 'ready' | 'failed' | 'deleted';
    tags?: Array<string>;
    thread_ref?: string | null;
    title?: string | null;
    version_id: string;
    version_no: number;
};

