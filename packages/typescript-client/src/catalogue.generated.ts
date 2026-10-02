/* Generated from Python; regenerate with npm run generate. */
export default {
  "ingestWithoutPath": {
    "additionalProperties": false,
    "oneOf": [
      {
        "not": {
          "anyOf": [
            {
              "required": [
                "content_base64"
              ]
            }
          ]
        },
        "required": [
          "text",
          "filename"
        ]
      },
      {
        "not": {
          "anyOf": [
            {
              "required": [
                "text"
              ]
            }
          ]
        },
        "required": [
          "content_base64",
          "filename"
        ]
      }
    ],
    "properties": {
      "content_base64": {
        "description": "Standard base64-encoded bytes (no data: URL prefix). Mutually exclusive with path and text. Requires filename. Use for binary; for plain text prefer text.",
        "minLength": 1,
        "type": "string"
      },
      "filename": {
        "description": "Required when text or content_base64 is used. Optional with path (defaults to the path basename). Does not change mime inference for path mode \u2014 mime follows the real path name unless mime is set.",
        "maxLength": 512,
        "minLength": 1,
        "type": "string"
      },
      "mime": {
        "description": "Optional; an explicit value always wins. Default: for path, inferred from the real path name; for content_base64, inferred from filename (.md \u2192 text/markdown, .pdf \u2192 application/pdf, .png \u2192 image/png, \u2026), else application/octet-stream; for text, a text/* type inferred from filename, else text/plain.",
        "maxLength": 255,
        "minLength": 1,
        "type": "string"
      },
      "source_kind": {
        "description": "Lineage class (e.g. agent, cli, feeder). Must be paired with source_ref. Prefer setting this for durable agent memory.",
        "maxLength": 128,
        "minLength": 1,
        "type": "string"
      },
      "source_modified_at": {
        "description": "Optional ISO-8601 UTC timestamp (timezone-aware). Requires source_kind/source_ref.",
        "type": "string"
      },
      "source_ref": {
        "description": "Stable id within source_kind. Reuse creates a new version of the same document when bytes change (engine D55 / SDK contract).",
        "maxLength": 512,
        "minLength": 1,
        "type": "string"
      },
      "source_version_ref": {
        "description": "Optional upstream revision label. Requires source_kind/source_ref.",
        "maxLength": 512,
        "minLength": 1,
        "type": "string"
      },
      "text": {
        "description": "UTF-8 document body. Mutually exclusive with path and content_base64. Requires filename.",
        "minLength": 1,
        "type": "string"
      },
      "title": {
        "description": "Optional human title forwarded to the engine.",
        "maxLength": 512,
        "type": "string"
      },
      "versioning_mode": {
        "default": "snapshot",
        "description": "Requires source_kind/source_ref when not snapshot.",
        "enum": [
          "snapshot",
          "living"
        ],
        "type": "string"
      }
    },
    "type": "object"
  },
  "projectArgument": {
    "description": "Which project's memory to use, by id or name. Omit it to use the default project.",
    "maxLength": 200,
    "minLength": 1,
    "type": "string"
  },
  "tools": [
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": false
      },
      "description": "Store a document into this deployment's memory (E0 write). Returns a version_id immediately; the indexing pipeline is asynchronous and may take many minutes (structure alone has been measured at ~11 minutes on a ~2.5KB file). Do NOT call assured recall operations expecting this content until pipeline_readiness reports ready=true for the version_id. Prefer source_kind plus a stable source_ref for durable agent memory so later writes become new versions of the same document; omit both only for intentionally anonymous one-shot ingest. Body sources (exactly one): text for short UTF-8 notes already in context; content_base64 for binary; path, where this server offers it, only when the operator has configured REMEMBERSTACK_MCP_INGEST_ROOTS allowlisted directories on this MCP host \u2014 with no roots configured, path is rejected (use text/content_base64 or ask the operator to set roots). Path reads resolve fully, must stay inside a configured root after symlink resolution, must be regular files, and are size-bounded (served capability limit when present, otherwise a local process resource guard). Bodies must be non-empty; deployments may enforce a maximum body size (oversized or empty bodies map to structured body_too_large / empty_body errors). source_kind and source_ref must be supplied together when either is set (stable lineage). If the result has parked=\"no_route\", the original is stored but its conversion is parked waiting for a conversion route for its MIME type. Tell the user now instead of polling readiness.",
      "destructive": false,
      "http_route": "POST /ingest",
      "input_schema": {
        "additionalProperties": false,
        "oneOf": [
          {
            "not": {
              "anyOf": [
                {
                  "required": [
                    "text"
                  ]
                },
                {
                  "required": [
                    "content_base64"
                  ]
                }
              ]
            },
            "required": [
              "path"
            ]
          },
          {
            "not": {
              "anyOf": [
                {
                  "required": [
                    "path"
                  ]
                },
                {
                  "required": [
                    "content_base64"
                  ]
                }
              ]
            },
            "required": [
              "text",
              "filename"
            ]
          },
          {
            "not": {
              "anyOf": [
                {
                  "required": [
                    "path"
                  ]
                },
                {
                  "required": [
                    "text"
                  ]
                }
              ]
            },
            "required": [
              "content_base64",
              "filename"
            ]
          }
        ],
        "properties": {
          "content_base64": {
            "description": "Standard base64-encoded bytes (no data: URL prefix). Mutually exclusive with path and text. Requires filename. Use for binary; for plain text prefer text.",
            "minLength": 1,
            "type": "string"
          },
          "filename": {
            "description": "Required when text or content_base64 is used. Optional with path (defaults to the path basename). Does not change mime inference for path mode \u2014 mime follows the real path name unless mime is set.",
            "maxLength": 512,
            "minLength": 1,
            "type": "string"
          },
          "mime": {
            "description": "Optional; an explicit value always wins. Default: for path, inferred from the real path name; for content_base64, inferred from filename (.md \u2192 text/markdown, .pdf \u2192 application/pdf, .png \u2192 image/png, \u2026), else application/octet-stream; for text, a text/* type inferred from filename, else text/plain.",
            "maxLength": 255,
            "minLength": 1,
            "type": "string"
          },
          "path": {
            "description": "Local filesystem path readable by this MCP process, only when REMEMBERSTACK_MCP_INGEST_ROOTS is configured. Mutually exclusive with text and content_base64. Path is resolved fully; symlink escape outside a configured root is rejected. Must be a regular file (not a directory, FIFO, or device). Size is checked before read. Filename defaults to the path basename; mime is inferred from the real path name unless mime is supplied (SDK parity).",
            "minLength": 1,
            "type": "string"
          },
          "source_kind": {
            "description": "Lineage class (e.g. agent, cli, feeder). Must be paired with source_ref. Prefer setting this for durable agent memory.",
            "maxLength": 128,
            "minLength": 1,
            "type": "string"
          },
          "source_modified_at": {
            "description": "Optional ISO-8601 UTC timestamp (timezone-aware). Requires source_kind/source_ref.",
            "type": "string"
          },
          "source_ref": {
            "description": "Stable id within source_kind. Reuse creates a new version of the same document when bytes change (engine D55 / SDK contract).",
            "maxLength": 512,
            "minLength": 1,
            "type": "string"
          },
          "source_version_ref": {
            "description": "Optional upstream revision label. Requires source_kind/source_ref.",
            "maxLength": 512,
            "minLength": 1,
            "type": "string"
          },
          "text": {
            "description": "UTF-8 document body. Mutually exclusive with path and content_base64. Requires filename.",
            "minLength": 1,
            "type": "string"
          },
          "title": {
            "description": "Optional human title forwarded to the engine.",
            "maxLength": 512,
            "type": "string"
          },
          "versioning_mode": {
            "default": "snapshot",
            "description": "Requires source_kind/source_ref when not snapshot.",
            "enum": [
              "snapshot",
              "living"
            ],
            "type": "string"
          }
        },
        "type": "object"
      },
      "name": "ingest",
      "permission": "memory:write",
      "tool_version": 1
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "Inspect whether one or more document version_ids have finished the requested pipeline and serving capabilities and are safe to recall. Call after ingest with the returned version_id. ready=true means assured recall operations may see the content (subject to retrieval relevance). require must explicitly name all four capabilities: pipeline, p1, live_graph, and p3. For ordinary recall polling require pipeline, p1, and live_graph, but set p3=false unless a published CorpusFS snapshot is part of the caller's contract. The live graph is PostgreSQL state and never waits for a projection build. Terminal stop: if any stages[].status is dead_letter, STOP polling and report the version_id and that stage to the user \u2014 a dead-lettered stage has used all its retries and never becomes ready by waiting. status=failed is NOT terminal: the last attempt failed and a retry is scheduled with back-off, so keep polling and describe it as retrying. Bounded poll: wait ~30s after ingest, then poll every 30\u201360s with mild back-off (floor ~15s). After ~20\u201330 minutes without ready=true and without a dead_letter stage, stop and escalate to the operator (include version_id and last stages[]). An ingest that returned created=false started no new run, but an earlier run of the same bytes may still be processing: poll that version_id the same way.",
      "destructive": false,
      "http_route": "POST /readiness",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "require": {
            "additionalProperties": false,
            "description": "Exhaustive capability request. Ordinary recall polling uses pipeline=true, p1=true, live_graph=true, p3=false.",
            "properties": {
              "live_graph": {
                "type": "boolean"
              },
              "p1": {
                "type": "boolean"
              },
              "p3": {
                "type": "boolean"
              },
              "pipeline": {
                "type": "boolean"
              }
            },
            "required": [
              "pipeline",
              "p1",
              "live_graph",
              "p3"
            ],
            "type": "object"
          },
          "version_ids": {
            "description": "Document version UUIDs from ingest.",
            "items": {
              "minLength": 1,
              "type": "string"
            },
            "maxItems": 1000,
            "minItems": 1,
            "type": "array"
          }
        },
        "required": [
          "version_ids",
          "require"
        ],
        "type": "object"
      },
      "name": "pipeline_readiness",
      "permission": "memory:read",
      "tool_version": 1
    },
    {
      "annotations": {
        "destructiveHint": true,
        "readOnlyHint": false
      },
      "description": "Remove one document from this deployment's memory. Use only when the user asks to delete or forget a specific document, or it is plainly wrong or unwanted \u2014 never to tidy up, and never to change a fact (ingest a correcting document instead). Takes the doc_id that ingest returned or that a claim or source cites. The effect is immediate: the document leaves search, facts and the document list; its claims stop counting as evidence; facts that no other document supports are closed. Facts other documents also support stay. The claims and stored original are kept as history, so this is not an erasure. Ingesting the same document again later adds it back as a new version. Returns claims_retired, relations_closed and observations_closed. A document_not_found error means the id is unknown or the document is already deleted: do not retry it.",
      "destructive": true,
      "http_route": "DELETE /documents/{doc_id}",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "doc_id": {
            "description": "The document's UUID (doc_id), as ingest returned it.",
            "minLength": 1,
            "type": "string"
          }
        },
        "required": [
          "doc_id"
        ],
        "type": "object"
      },
      "name": "delete_document",
      "permission": "memory:write",
      "tool_version": 1
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "Find files: documents by name, metadata and content. Use it when the user names or describes a file (\"find Q3_sales_2025.xlsx\", \"the audit report from last spring\", \"emails from Alice\") rather than asking about its content. query matches every name a document was stored under (file name, title, source path; old names too after a rename; partial and misspelled names work) and its text. Filters narrow by family (text, markdown, html, pdf, image, audio, video, office, other), authors, recipients (a name or an address; \"alice\" matches \"Alice Novak\"), created/modified date ranges, language, thread_ref and doc_ids. Each result is a document judged by its current version (versions=\"all\" searches every live version and returns the newest match); it carries doc_id, version_id, file_name, title, family, processing status, authors, recipients, dates, p3_path (documents/<doc_id> in the corpus filesystem view, where one is published) and a short overview when one exists. When a people filter matches several different people, people_matched lists each with a document count: narrow the filter (for example by address) instead of guessing. Without query, results are newest first and cursor pages them.",
      "destructive": false,
      "http_route": "POST /documents/search",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "authors": {
            "description": "Any of these authors.",
            "items": {
              "minLength": 1,
              "type": "string"
            },
            "type": "array"
          },
          "created_from": {
            "description": "An ISO 8601 instant with a timezone, inclusive.",
            "format": "date-time",
            "type": "string"
          },
          "created_to": {
            "description": "An ISO 8601 instant with a timezone, inclusive.",
            "format": "date-time",
            "type": "string"
          },
          "cursor": {
            "description": "The previous page's cursor; only without query.",
            "minLength": 1,
            "type": "string"
          },
          "doc_ids": {
            "description": "Only these documents (UUIDs).",
            "items": {
              "minLength": 1,
              "type": "string"
            },
            "type": "array"
          },
          "family": {
            "description": "Keep these format families.",
            "items": {
              "minLength": 1,
              "type": "string"
            },
            "type": "array"
          },
          "k": {
            "maximum": 200,
            "minimum": 1,
            "type": "integer"
          },
          "language": {
            "minLength": 1,
            "type": "string"
          },
          "modified_from": {
            "description": "An ISO 8601 instant with a timezone, inclusive.",
            "format": "date-time",
            "type": "string"
          },
          "modified_to": {
            "description": "An ISO 8601 instant with a timezone, inclusive.",
            "format": "date-time",
            "type": "string"
          },
          "query": {
            "description": "Words from the file's name, title, path or text.",
            "maxLength": 4096,
            "minLength": 1,
            "type": "string"
          },
          "recipients": {
            "description": "Any of these recipients.",
            "items": {
              "minLength": 1,
              "type": "string"
            },
            "type": "array"
          },
          "thread_ref": {
            "minLength": 1,
            "type": "string"
          },
          "versions": {
            "description": "current (default) or all live versions.",
            "enum": [
              "current",
              "all"
            ],
            "type": "string"
          }
        },
        "type": "object"
      },
      "name": "search_documents",
      "permission": "memory:read",
      "tool_version": 1
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "Resolve a name to ranked current survivor candidates; never silently guess.",
      "destructive": false,
      "http_route": "POST /operations/resolve_entity",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "name": {
            "minLength": 1,
            "type": "string"
          }
        },
        "required": [
          "name"
        ],
        "type": "object"
      },
      "name": "resolve_entity",
      "permission": "memory:read",
      "tool_version": 1
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "High-recall current claims and confirmed source passages.",
      "destructive": false,
      "http_route": "POST /operations/claims_and_sources_context",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "candidate_k": {
            "default": 200,
            "maximum": 400,
            "minimum": 1,
            "type": "integer"
          },
          "entity_ids": {
            "items": {
              "format": "uuid",
              "type": "string"
            },
            "maxItems": 20,
            "minItems": 1,
            "type": "array",
            "uniqueItems": true
          },
          "k": {
            "default": 50,
            "maximum": 100,
            "minimum": 1,
            "type": "integer"
          },
          "query": {
            "maxLength": 8192,
            "minLength": 1,
            "type": "string"
          }
        },
        "required": [
          "query"
        ],
        "type": "object"
      },
      "name": "claims_and_sources_context",
      "permission": "memory:read",
      "tool_version": 2
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "Adjudicated relations and observations under an explicit world-time scope, with bounded live-graph expansion for current or point-in-time entity anchors.",
      "destructive": false,
      "http_route": "POST /operations/facts_context",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "entity_ids": {
            "items": {
              "format": "uuid",
              "type": "string"
            },
            "maxItems": 19,
            "minItems": 1,
            "type": "array",
            "uniqueItems": true
          },
          "evidence_per_fact": {
            "default": 3,
            "maximum": 5,
            "minimum": 1,
            "type": "integer"
          },
          "hops": {
            "default": 1,
            "maximum": 2,
            "minimum": 1,
            "type": "integer"
          },
          "k": {
            "default": 15,
            "maximum": 30,
            "minimum": 1,
            "type": "integer"
          },
          "predicate": {
            "maxLength": 200,
            "minLength": 1,
            "type": "string"
          },
          "query": {
            "maxLength": 8192,
            "minLength": 1,
            "type": "string"
          },
          "time": {
            "default": {
              "mode": "current"
            },
            "oneOf": [
              {
                "additionalProperties": false,
                "properties": {
                  "mode": {
                    "const": "current"
                  }
                },
                "required": [
                  "mode"
                ]
              },
              {
                "additionalProperties": false,
                "properties": {
                  "at": {
                    "format": "date-time",
                    "type": "string"
                  },
                  "mode": {
                    "const": "at"
                  }
                },
                "required": [
                  "mode",
                  "at"
                ]
              },
              {
                "additionalProperties": false,
                "properties": {
                  "from": {
                    "format": "date-time",
                    "type": "string"
                  },
                  "mode": {
                    "const": "overlap"
                  },
                  "to": {
                    "format": "date-time",
                    "type": "string"
                  }
                },
                "required": [
                  "mode",
                  "from",
                  "to"
                ]
              },
              {
                "additionalProperties": false,
                "properties": {
                  "mode": {
                    "const": "history"
                  }
                },
                "required": [
                  "mode"
                ]
              }
            ],
            "type": "object"
          }
        },
        "required": [
          "query"
        ],
        "type": "object"
      },
      "name": "facts_context",
      "permission": "memory:read",
      "tool_version": 3
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "Complete claims-and-sources and neighborhood-aware fact responses side by side in ContextBundle/v2.",
      "destructive": false,
      "http_route": "POST /operations/combined_context",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "entity_ids": {
            "items": {
              "format": "uuid",
              "type": "string"
            },
            "maxItems": 19,
            "minItems": 1,
            "type": "array",
            "uniqueItems": true
          },
          "hops": {
            "default": 1,
            "maximum": 2,
            "minimum": 1,
            "type": "integer"
          },
          "predicate": {
            "maxLength": 200,
            "minLength": 1,
            "type": "string"
          },
          "query": {
            "maxLength": 8192,
            "minLength": 1,
            "type": "string"
          },
          "time": {
            "default": {
              "mode": "current"
            },
            "oneOf": [
              {
                "additionalProperties": false,
                "properties": {
                  "mode": {
                    "const": "current"
                  }
                },
                "required": [
                  "mode"
                ]
              },
              {
                "additionalProperties": false,
                "properties": {
                  "at": {
                    "format": "date-time",
                    "type": "string"
                  },
                  "mode": {
                    "const": "at"
                  }
                },
                "required": [
                  "mode",
                  "at"
                ]
              },
              {
                "additionalProperties": false,
                "properties": {
                  "from": {
                    "format": "date-time",
                    "type": "string"
                  },
                  "mode": {
                    "const": "overlap"
                  },
                  "to": {
                    "format": "date-time",
                    "type": "string"
                  }
                },
                "required": [
                  "mode",
                  "from",
                  "to"
                ]
              },
              {
                "additionalProperties": false,
                "properties": {
                  "mode": {
                    "const": "history"
                  }
                },
                "required": [
                  "mode"
                ]
              }
            ],
            "type": "object"
          }
        },
        "required": [
          "query"
        ],
        "type": "object"
      },
      "name": "combined_context",
      "permission": "memory:read",
      "tool_version": 4
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "Retrieve neighbouring chunks preceding and succeeding a target chunk within the same document to expand conversational or narrative context.",
      "destructive": false,
      "http_route": "GET /chunks/{chunk_id}/adjacent",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "chunk_id": {
            "description": "UUID of the target chunk to expand around.",
            "type": "string"
          },
          "window": {
            "default": 1,
            "description": "Number of neighbouring chunks to retrieve on each side (1 or 2, default 1).",
            "maximum": 2,
            "minimum": 1,
            "type": "integer"
          }
        },
        "required": [
          "chunk_id"
        ],
        "type": "object"
      },
      "name": "adjacent_chunks",
      "permission": "memory:read",
      "tool_version": 1
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "Run one sandboxed read-only SQL statement over the memory_v1 query space. Returns QueryResult/v1 (exploratory_tabular).",
      "destructive": false,
      "http_route": "POST /query/sql",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "max_rows": {
            "minimum": 0,
            "type": "integer"
          },
          "parameters": {
            "description": "Positional bound parameters ($1, $2, \u2026)",
            "items": {},
            "type": "array"
          },
          "sql": {
            "type": "string"
          }
        },
        "required": [
          "sql"
        ],
        "type": "object"
      },
      "name": "query_sql",
      "permission": "memory:read",
      "tool_version": 1
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "EXPLAIN (FORMAT JSON) one SQL statement without executing it; same parser, relation, function, and operator gates.",
      "destructive": false,
      "http_route": "POST /query/sql/explain",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "parameters": {
            "items": {},
            "type": "array"
          },
          "sql": {
            "type": "string"
          }
        },
        "required": [
          "sql"
        ],
        "type": "object"
      },
      "name": "explain_sql",
      "permission": "memory:read",
      "tool_version": 1
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "Manifest-backed exact schema, functions, comments, versions, hashes, and limits. Opens with the bound two-layer headline.",
      "destructive": false,
      "http_route": "GET /query/space",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "include_examples": {
            "default": false,
            "description": "When true, list shipped examples.* names",
            "type": "boolean"
          },
          "pattern": {
            "description": "Optional fnmatch filter over view names",
            "type": "string"
          }
        },
        "type": "object"
      },
      "name": "describe_query_space",
      "permission": "memory:read",
      "tool_version": 1
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "Search checked-in manifest text (names, comments, tags, examples); never tenant content. k in 1..25.",
      "destructive": false,
      "http_route": "GET /query/space/search",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "k": {
            "default": 10,
            "maximum": 25,
            "minimum": 1,
            "type": "integer"
          },
          "query": {
            "type": "string"
          }
        },
        "required": [
          "query"
        ],
        "type": "object"
      },
      "name": "search_query_space",
      "permission": "memory:read",
      "tool_version": 1
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "List saved-query registry metadata. Default lists active versions only; drafts require an explicit status filter.",
      "destructive": false,
      "http_route": "GET /query/saved",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "namespace": {
            "type": "string"
          },
          "status": {
            "type": "string"
          }
        },
        "type": "object"
      },
      "name": "list_saved_queries",
      "permission": "memory:read",
      "tool_version": 1
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "Describe one saved-query version: parameters, declared columns, validation state, and hashes.",
      "destructive": false,
      "http_route": "GET /query/saved/{namespace}/{name}",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "name": {
            "type": "string"
          },
          "namespace": {
            "type": "string"
          },
          "version": {
            "minimum": 1,
            "type": "integer"
          }
        },
        "required": [
          "namespace",
          "name"
        ],
        "type": "object"
      },
      "name": "describe_saved_query",
      "permission": "memory:read",
      "tool_version": 1
    },
    {
      "annotations": {
        "destructiveHint": false,
        "readOnlyHint": true
      },
      "description": "Execute one active saved query through the same SQL sandbox. Not a top-level intent operation; returns QueryResult/v1.",
      "destructive": false,
      "http_route": "POST /query/saved/{namespace}/{name}/run",
      "input_schema": {
        "additionalProperties": false,
        "properties": {
          "max_rows": {
            "minimum": 0,
            "type": "integer"
          },
          "name": {
            "type": "string"
          },
          "namespace": {
            "type": "string"
          },
          "parameters": {
            "items": {},
            "type": "array"
          },
          "version": {
            "minimum": 1,
            "type": "integer"
          }
        },
        "required": [
          "namespace",
          "name"
        ],
        "type": "object"
      },
      "name": "run_saved_query",
      "permission": "memory:read",
      "tool_version": 1
    }
  ]
};
