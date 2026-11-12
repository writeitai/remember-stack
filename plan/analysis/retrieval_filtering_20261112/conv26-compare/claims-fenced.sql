
    SELECT c.claim_id, c.doc_id, c.chunk_id, c.claim_text, c.source_span,
           c.char_start, c.char_end,
           COALESCE(occ.evidence_spans, '[]'::jsonb) AS evidence_spans,
           c.is_attributed,
           TRUE AS is_current_testimony,
           c.asserted_at, c.claim_valid_from, c.claim_valid_until,
           c.claim_valid_precision::text AS claim_valid_precision,
           c.claim_valid_kind::text AS claim_valid_kind,
           d.title AS document_title, d.source_kind,
           EXISTS (SELECT 1 FROM public.document_version_scope mode_row WHERE mode_row.deployment_id = %(deployment_id)s AND mode_row.doc_id = c.doc_id AND mode_row.periodised) AS periodised, scope.coverage
    FROM claims c
    JOIN memory_v1.documents_live d
      ON d.deployment_id = c.deployment_id AND d.doc_id = c.doc_id
    LEFT JOIN LATERAL (
        SELECT cc.evidence_spans
        FROM chunk_claims cc
        WHERE cc.deployment_id = c.deployment_id
          AND cc.claim_id = c.claim_id
          AND cc.chunk_id = c.chunk_id
        ORDER BY cc.created_at, cc.derivation_kind NULLS FIRST
        LIMIT 1
    ) AS occ ON true
    JOIN LATERAL (
        SELECT count(DISTINCT mention.resolved_entity_id)::integer AS coverage
        
        FROM mentions AS raw_mention
        CROSS JOIN LATERAL (
            SELECT live.deployment_id, live.claim_id, live.resolved_entity_id
            FROM memory_v1.mentions_live AS live
            WHERE live.deployment_id=raw_mention.deployment_id
              AND live.mention_id=raw_mention.mention_id
              AND live.claim_id=c.claim_id
            OFFSET 0
        ) AS mention
    
        WHERE raw_mention.deployment_id = c.deployment_id AND raw_mention.claim_id=c.claim_id AND mention.deployment_id = c.deployment_id
          AND mention.claim_id = c.claim_id
          AND mention.resolved_entity_id = ANY(CAST(%(entity_ids)s AS uuid[]))
    ) AS scope ON scope.coverage > 0
    WHERE c.deployment_id = %(deployment_id)s
      AND c.claim_id = ANY(%(claim_ids)s)
      AND c.is_current_testimony
      AND ((NOT EXISTS (SELECT 1 FROM public.document_version_scope mode_row WHERE mode_row.deployment_id = %(deployment_id)s AND mode_row.doc_id = c.doc_id AND mode_row.periodised) AND EXISTS (SELECT 1 FROM memory_v1.claims_live AS published WHERE published.deployment_id = c.deployment_id AND published.claim_id = c.claim_id)) OR EXISTS (SELECT 1 FROM public.chunk_claims occurrence JOIN public.chunks occurrence_chunk   ON occurrence_chunk.deployment_id = occurrence.deployment_id  AND occurrence_chunk.chunk_id = occurrence.chunk_id JOIN public.document_versions occurrence_version   ON occurrence_version.deployment_id = occurrence_chunk.deployment_id  AND occurrence_version.version_id = occurrence_chunk.version_id  AND occurrence_version.current_representation_id      = occurrence_chunk.representation_id JOIN public.document_version_scope occurrence_scope   ON occurrence_scope.deployment_id = occurrence_chunk.deployment_id  AND occurrence_scope.version_id = occurrence_chunk.version_id WHERE occurrence.deployment_id = %(deployment_id)s   AND occurrence.claim_id = c.claim_id   AND occurrence_scope.periodised   AND occurrence_scope.selectable   AND occurrence_scope.in_force && tstzrange(CAST(%(scope_lower)s AS timestamptz), CAST(%(scope_upper)s AS timestamptz), '[]')))
    
