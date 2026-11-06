/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EffectiveInterval } from './EffectiveInterval';
/**
 * One candidate edition of a periodised document that matched (D140 §3.5).
 *
 * ``version_id`` with ``representation_id`` is the ``source_open`` handle
 * that opens exactly this edition.
 */
export type MatchingEdition = {
    effective?: Array<EffectiveInterval>;
    representation_id?: string | null;
    version_id: string;
    version_key?: string | null;
    version_no: number;
};

