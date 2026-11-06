/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputReferenceGeneration } from './OutputReferenceGeneration';
/**
 * What one ``PUT …/references`` did.
 *
 * ``outcome``: ``created`` — a new generation was recorded (``pending``,
 * or ``rejected`` when the version's structure already showed an unknown
 * source section); ``unchanged`` — the body equals the version's current
 * intent (a retry); ``pending_cancelled`` — the body equals the active
 * set, so the newer pending generation was cancelled. ``generation`` is
 * the generation the caller's intent now names.
 */
export type OutputReferencesSet = {
    doc_id: string;
    generation: OutputReferenceGeneration;
    outcome: 'created' | 'unchanged' | 'pending_cancelled';
    version_id: string;
};

