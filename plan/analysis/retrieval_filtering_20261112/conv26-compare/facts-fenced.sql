
            WITH RECURSIVE 
scoped_entities(entity_id, survivor_entity_id) AS MATERIALIZED (
    SELECT entity_id, entity_id
    FROM entities
    WHERE deployment_id = %(deployment_id)s
      AND entity_id = ANY(CAST(%(entity_ids)s AS uuid[]))
      AND merged_into IS NULL
    UNION ALL
    SELECT redirect.entity_id, scoped.survivor_entity_id
    FROM scoped_entities AS scoped
    JOIN entities AS redirect ON redirect.merged_into = scoped.entity_id
    WHERE redirect.deployment_id = %(deployment_id)s
)
, candidates AS (
                (SELECT indexed.relation_id::text AS item_id,
                        'relation'::text AS qualifier,
                        
                        (CASE
                             WHEN subject_scope.survivor_entity_id
                                  = ANY(CAST(%(ranking_entity_ids)s AS uuid[]))
                             THEN 1 ELSE 0
                           END
                         + CASE
                             WHEN object_scope.entity_id IS NULL
                               OR object_scope.survivor_entity_id
                                  = subject_scope.survivor_entity_id
                               OR object_scope.survivor_entity_id
                                  <> ALL(CAST(%(ranking_entity_ids)s AS uuid[]))
                             THEN 0 ELSE 1
                           END) AS coverage,
                
                        indexed.embedding <=> CAST(%(query_vector)s AS vector) AS distance
                 FROM relations AS indexed
                 
                 LEFT JOIN scoped_entities AS subject_scope
                   ON subject_scope.entity_id = indexed.subject_entity_id
                 LEFT JOIN scoped_entities AS object_scope
                   ON object_scope.entity_id = indexed.object_entity_id
                
                  JOIN LATERAL (SELECT 1 FROM memory_v1.facts_visible_history AS eligible WHERE eligible.deployment_id=indexed.deployment_id AND eligible.fact_kind='relation' AND eligible.fact_id=indexed.relation_id OFFSET 0) AS fact ON true
                 WHERE indexed.deployment_id = %(deployment_id)s
                   AND indexed.embedding IS NOT NULL
                   AND vector_norm(indexed.embedding) > 0
                   AND indexed.embedding_model = %(embedding_model)s
                   AND indexed.embedding_input_policy_version = %(input_policy)s
                   AND indexed.ingested_at <= %(evaluated_at)s AND indexed.invalidated_at IS NULL AND (indexed.valid_from IS NULL OR indexed.valid_from <= %(evaluated_at)s) AND (indexed.valid_until IS NULL OR indexed.valid_until > %(evaluated_at)s) AND (subject_scope.entity_id IS NOT NULL OR object_scope.entity_id IS NOT NULL)
                   AND (EXISTS (SELECT 1 FROM public.relation_evidence gate_relation_evidence JOIN public.chunk_claims gate_relation_occurrence   ON gate_relation_occurrence.deployment_id = gate_relation_evidence.deployment_id  AND gate_relation_occurrence.claim_id = gate_relation_evidence.claim_id JOIN public.chunks gate_relation_chunk   ON gate_relation_chunk.deployment_id = gate_relation_occurrence.deployment_id  AND gate_relation_chunk.chunk_id = gate_relation_occurrence.chunk_id JOIN public.document_versions gate_relation_version   ON gate_relation_version.deployment_id = gate_relation_chunk.deployment_id  AND gate_relation_version.version_id = gate_relation_chunk.version_id  AND gate_relation_version.current_representation_id = gate_relation_chunk.representation_id JOIN public.document_version_scope gate_relation_scope   ON gate_relation_scope.deployment_id = gate_relation_chunk.deployment_id  AND gate_relation_scope.version_id = gate_relation_chunk.version_id WHERE gate_relation_evidence.deployment_id = %(deployment_id)s   AND gate_relation_evidence.relation_id = indexed.relation_id   AND gate_relation_evidence.stance = 'supports' AND (NOT gate_relation_scope.periodised OR (gate_relation_scope.selectable AND gate_relation_scope.in_force && tstzrange(CAST(%(scope_lower)s AS timestamptz), CAST(%(scope_upper)s AS timestamptz), '[]')))) OR (NOT EXISTS (SELECT 1 FROM public.relation_evidence gate_relation_any WHERE gate_relation_any.deployment_id = %(deployment_id)s AND gate_relation_any.relation_id = indexed.relation_id AND gate_relation_any.stance = 'supports') AND EXISTS (SELECT 1 FROM public.relation_evidence gate_relation_all_evidence JOIN public.chunk_claims gate_relation_all_occurrence   ON gate_relation_all_occurrence.deployment_id = gate_relation_all_evidence.deployment_id  AND gate_relation_all_occurrence.claim_id = gate_relation_all_evidence.claim_id JOIN public.chunks gate_relation_all_chunk   ON gate_relation_all_chunk.deployment_id = gate_relation_all_occurrence.deployment_id  AND gate_relation_all_chunk.chunk_id = gate_relation_all_occurrence.chunk_id JOIN public.document_versions gate_relation_all_version   ON gate_relation_all_version.deployment_id = gate_relation_all_chunk.deployment_id  AND gate_relation_all_version.version_id = gate_relation_all_chunk.version_id  AND gate_relation_all_version.current_representation_id = gate_relation_all_chunk.representation_id JOIN public.document_version_scope gate_relation_all_scope   ON gate_relation_all_scope.deployment_id = gate_relation_all_chunk.deployment_id  AND gate_relation_all_scope.version_id = gate_relation_all_chunk.version_id WHERE gate_relation_all_evidence.deployment_id = %(deployment_id)s   AND gate_relation_all_evidence.relation_id = indexed.relation_id AND (NOT gate_relation_all_scope.periodised OR (gate_relation_all_scope.selectable AND gate_relation_all_scope.in_force && tstzrange(CAST(%(scope_lower)s AS timestamptz), CAST(%(scope_upper)s AS timestamptz), '[]'))))))
                 ORDER BY coverage DESC, indexed.embedding <=> CAST(%(query_vector)s AS vector),
                          indexed.relation_id
                 LIMIT %(branch_limit)s)
                 UNION ALL 
                (SELECT indexed.observation_id::text AS item_id,
                        'observation'::text AS qualifier,
                        
                        CASE
                            WHEN subject_scope.survivor_entity_id
                                 = ANY(CAST(%(ranking_entity_ids)s AS uuid[]))
                            THEN 1 ELSE 0
                        END AS coverage,
                
                        indexed.embedding <=> CAST(%(query_vector)s AS vector) AS distance
                 FROM observations AS indexed
                 
                 JOIN scoped_entities AS subject_scope
                   ON subject_scope.entity_id = indexed.subject_entity_id
                
                  JOIN LATERAL (SELECT 1 FROM memory_v1.facts_visible_history AS eligible WHERE eligible.deployment_id=indexed.deployment_id AND eligible.fact_kind='observation' AND eligible.fact_id=indexed.observation_id OFFSET 0) AS fact ON true
                 WHERE indexed.deployment_id = %(deployment_id)s
                   AND indexed.embedding IS NOT NULL
                   AND vector_norm(indexed.embedding) > 0
                   AND indexed.embedding_model = %(embedding_model)s
                   AND indexed.embedding_input_policy_version = %(input_policy)s
                   AND indexed.ingested_at <= %(evaluated_at)s AND indexed.invalidated_at IS NULL AND (indexed.valid_from IS NULL OR indexed.valid_from <= %(evaluated_at)s) AND (indexed.valid_until IS NULL OR indexed.valid_until > %(evaluated_at)s) 
                   AND (EXISTS (SELECT 1 FROM public.observation_evidence gate_observation_evidence JOIN public.chunk_claims gate_observation_occurrence   ON gate_observation_occurrence.deployment_id = gate_observation_evidence.deployment_id  AND gate_observation_occurrence.claim_id = gate_observation_evidence.claim_id JOIN public.chunks gate_observation_chunk   ON gate_observation_chunk.deployment_id = gate_observation_occurrence.deployment_id  AND gate_observation_chunk.chunk_id = gate_observation_occurrence.chunk_id JOIN public.document_versions gate_observation_version   ON gate_observation_version.deployment_id = gate_observation_chunk.deployment_id  AND gate_observation_version.version_id = gate_observation_chunk.version_id  AND gate_observation_version.current_representation_id = gate_observation_chunk.representation_id JOIN public.document_version_scope gate_observation_scope   ON gate_observation_scope.deployment_id = gate_observation_chunk.deployment_id  AND gate_observation_scope.version_id = gate_observation_chunk.version_id WHERE gate_observation_evidence.deployment_id = %(deployment_id)s   AND gate_observation_evidence.observation_id = indexed.observation_id   AND gate_observation_evidence.stance = 'supports' AND (NOT gate_observation_scope.periodised OR (gate_observation_scope.selectable AND gate_observation_scope.in_force && tstzrange(CAST(%(scope_lower)s AS timestamptz), CAST(%(scope_upper)s AS timestamptz), '[]')))) OR (NOT EXISTS (SELECT 1 FROM public.observation_evidence gate_observation_any WHERE gate_observation_any.deployment_id = %(deployment_id)s AND gate_observation_any.observation_id = indexed.observation_id AND gate_observation_any.stance = 'supports') AND EXISTS (SELECT 1 FROM public.observation_evidence gate_observation_all_evidence JOIN public.chunk_claims gate_observation_all_occurrence   ON gate_observation_all_occurrence.deployment_id = gate_observation_all_evidence.deployment_id  AND gate_observation_all_occurrence.claim_id = gate_observation_all_evidence.claim_id JOIN public.chunks gate_observation_all_chunk   ON gate_observation_all_chunk.deployment_id = gate_observation_all_occurrence.deployment_id  AND gate_observation_all_chunk.chunk_id = gate_observation_all_occurrence.chunk_id JOIN public.document_versions gate_observation_all_version   ON gate_observation_all_version.deployment_id = gate_observation_all_chunk.deployment_id  AND gate_observation_all_version.version_id = gate_observation_all_chunk.version_id  AND gate_observation_all_version.current_representation_id = gate_observation_all_chunk.representation_id JOIN public.document_version_scope gate_observation_all_scope   ON gate_observation_all_scope.deployment_id = gate_observation_all_chunk.deployment_id  AND gate_observation_all_scope.version_id = gate_observation_all_chunk.version_id WHERE gate_observation_all_evidence.deployment_id = %(deployment_id)s   AND gate_observation_all_evidence.observation_id = indexed.observation_id AND (NOT gate_observation_all_scope.periodised OR (gate_observation_all_scope.selectable AND gate_observation_all_scope.in_force && tstzrange(CAST(%(scope_lower)s AS timestamptz), CAST(%(scope_upper)s AS timestamptz), '[]'))))))
                 ORDER BY coverage DESC, indexed.embedding <=> CAST(%(query_vector)s AS vector),
                          indexed.observation_id
                 LIMIT %(branch_limit)s)
                )
            SELECT item_id, qualifier, 1.0 - distance AS score
            FROM candidates
            ORDER BY coverage DESC, distance, qualifier, item_id LIMIT %(limit)s
        
