/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The three processing layers a request may read (D143).
 *
 * ``sources`` (documents, sections, chunks) is always processed; ``claims``
 * and ``facts`` can be switched off per deployment.
 */
export type ProcessingLayer = 'sources' | 'claims' | 'facts';
