/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ReferenceWindow } from './ReferenceWindow';
/**
 * The source side of one reference row.
 */
export type DocumentReferenceSource = {
    doc_id: string;
    section_key?: string | null;
    section_title?: string | null;
    version_id: string;
    version_key?: string | null;
    window: ReferenceWindow;
};

