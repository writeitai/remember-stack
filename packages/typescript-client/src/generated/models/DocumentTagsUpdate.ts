/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Change one document's tags atomically: ``add``/``remove``, or ``set``.
 *
 * ``set`` replaces the whole set and excludes ``add`` and ``remove``; an
 * empty ``set`` clears the document's tags. Removing a tag the document
 * does not have is not an error.
 */
export type DocumentTagsUpdate = {
    add?: Array<string>;
    remove?: Array<string>;
    set?: Array<string> | null;
};

