/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputDocumentReferenceSource } from './OutputDocumentReferenceSource';
import type { OutputDocumentReferenceTarget } from './OutputDocumentReferenceTarget';
import type { OutputNamedReferenceTarget } from './OutputNamedReferenceTarget';
/**
 * One reference, resolved against one source window and target version.
 */
export type OutputDocumentReference = {
    binding: 'floating' | 'pinned';
    change_date_known: boolean | null;
    change_effective_from: string | null;
    context: string | null;
    crossref_id: string;
    direction: 'outgoing' | 'incoming';
    kind: 'cites' | 'links_to' | 'attaches' | 'replies_to' | 'refers_to' | 'amends' | 'implements';
    named_target: OutputNamedReferenceTarget;
    origin: 'supplied' | 'extracted';
    source: OutputDocumentReferenceSource;
    source_label: string | null;
    status: 'resolved' | 'target_processing' | 'target_unavailable' | 'target_not_in_force' | 'section_not_in_version' | 'section_not_indexed' | 'pinned_version_unavailable';
    target: OutputDocumentReferenceTarget | null;
};
