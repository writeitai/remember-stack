/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ReferenceTarget } from './ReferenceTarget';
/**
 * One line of a supplied NDJSON reference set (D140 §6.3).
 *
 * ``from_section_key`` is the section of the source version the reference
 * is made from (omitted: the whole document). A ``pinned`` reference names
 * the target's ``version_key``. ``amends`` must say whether its date is
 * known: ``change_date_known = true`` with ``change_effective_from``, or
 * ``false`` without it; other kinds carry neither.
 */
export type ReferenceInput = {
    binding?: 'floating' | 'pinned';
    change_date_known?: boolean | null;
    change_effective_from?: string | null;
    context?: string | null;
    from_section_key?: string | null;
    kind: 'cites' | 'links_to' | 'attaches' | 'replies_to' | 'refers_to' | 'amends' | 'implements';
    source_label?: string | null;
    target: ReferenceTarget;
};

