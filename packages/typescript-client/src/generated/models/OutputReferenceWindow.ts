/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A time window: from ``from`` (inclusive; null = unbounded) to ``until``.
 *
 * ``until`` is exclusive unless ``until_inclusive`` (an instant window has
 * ``from == until`` and ``until_inclusive = true``); null is unbounded.
 */
export type OutputReferenceWindow = {
    from: string | null;
    until: string | null;
    until_inclusive: boolean;
};

