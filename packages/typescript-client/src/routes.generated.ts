/* Generated from Python; regenerate with npm run generate. */
export default {
  "DELETE /documents/{doc_id}": {
    "method": "DELETE",
    "operationId": "delete_document_documents__doc_id__delete",
    "parameters": [
      {
        "in": "path",
        "name": "doc_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Doc Id",
          "type": "string"
        }
      }
    ],
    "path": "/documents/{doc_id}",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/DocumentDeletion"
            }
          }
        },
        "description": "Successful Response"
      },
      "404": {
        "description": "document_not_found"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "DELETE /documents/{doc_id}/effective-periods": {
    "method": "DELETE",
    "operationId": "clear_effective_time_documents__doc_id__effective_periods_delete",
    "parameters": [
      {
        "in": "path",
        "name": "doc_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Doc Id",
          "type": "string"
        }
      }
    ],
    "path": "/documents/{doc_id}/effective-periods",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/EffectiveTimeCleared"
            }
          }
        },
        "description": "Successful Response"
      },
      "404": {
        "description": "document_not_found"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /chunks/{chunk_id}/adjacent": {
    "method": "GET",
    "operationId": "adjacent_chunks_chunks__chunk_id__adjacent_get",
    "parameters": [
      {
        "in": "path",
        "name": "chunk_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Chunk Id",
          "type": "string"
        }
      },
      {
        "in": "query",
        "name": "window",
        "required": false,
        "schema": {
          "default": 1,
          "maximum": 2,
          "minimum": 1,
          "title": "Window",
          "type": "integer"
        }
      }
    ],
    "path": "/chunks/{chunk_id}/adjacent",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /connectors": {
    "method": "GET",
    "optionalProfile": "connectors",
    "path": "/connectors"
  },
  "GET /connectors/{connector_id}": {
    "method": "GET",
    "optionalProfile": "connectors",
    "path": "/connectors/{connector_id}"
  },
  "GET /deployment": {
    "method": "GET",
    "operationId": "deployment_build_info_deployment_get",
    "parameters": [],
    "path": "/deployment",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/DeploymentBuildInfo"
            }
          }
        },
        "description": "Successful Response"
      }
    }
  },
  "GET /documents": {
    "method": "GET",
    "operationId": "list_documents_documents_get",
    "parameters": [
      {
        "in": "query",
        "name": "limit",
        "required": false,
        "schema": {
          "default": 50,
          "maximum": 200,
          "minimum": 1,
          "title": "Limit",
          "type": "integer"
        }
      },
      {
        "in": "query",
        "name": "cursor",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Cursor"
        }
      },
      {
        "in": "query",
        "name": "status",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "enum": [
                "ingesting",
                "converting",
                "structuring",
                "ready",
                "failed"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Status"
        }
      }
    ],
    "path": "/documents",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/DocumentPage"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /documents/{doc_id}/sections/{section_key}/history": {
    "method": "GET",
    "operationId": "section_history_documents__doc_id__sections__section_key__history_get",
    "parameters": [
      {
        "in": "path",
        "name": "doc_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Doc Id",
          "type": "string"
        }
      },
      {
        "in": "path",
        "name": "section_key",
        "required": true,
        "schema": {
          "title": "Section Key",
          "type": "string"
        }
      },
      {
        "in": "query",
        "name": "mode",
        "required": false,
        "schema": {
          "default": "history",
          "enum": [
            "current",
            "at",
            "overlap",
            "history"
          ],
          "title": "Mode",
          "type": "string"
        }
      },
      {
        "in": "query",
        "name": "at",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "format": "date-time",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "At"
        }
      },
      {
        "in": "query",
        "name": "from",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "format": "date-time",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "From"
        }
      },
      {
        "in": "query",
        "name": "to",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "format": "date-time",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "To"
        }
      },
      {
        "in": "query",
        "name": "k",
        "required": false,
        "schema": {
          "default": 50,
          "maximum": 200,
          "minimum": 1,
          "title": "K",
          "type": "integer"
        }
      },
      {
        "in": "query",
        "name": "cursor",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Cursor"
        }
      }
    ],
    "path": "/documents/{doc_id}/sections/{section_key}/history",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/SectionHistoryPage"
            }
          }
        },
        "description": "Successful Response"
      },
      "400": {
        "description": "cursor is malformed or belongs to another call"
      },
      "404": {
        "description": "document_not_found"
      },
      "422": {
        "description": "invalid section key or time scope"
      }
    }
  },
  "GET /documents/{doc_id}/versions/{version_id}/references": {
    "method": "GET",
    "operationId": "reference_generations_documents__doc_id__versions__version_id__references_get",
    "parameters": [
      {
        "in": "path",
        "name": "doc_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Doc Id",
          "type": "string"
        }
      },
      {
        "in": "path",
        "name": "version_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Version Id",
          "type": "string"
        }
      }
    ],
    "path": "/documents/{doc_id}/versions/{version_id}/references",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/ReferenceGenerations"
            }
          }
        },
        "description": "Successful Response"
      },
      "404": {
        "description": "document_not_found | version_not_found"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /hydrate/relation/{relation_id}": {
    "method": "GET",
    "operationId": "hydrate_relation_hydrate_relation__relation_id__get",
    "parameters": [
      {
        "in": "path",
        "name": "relation_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Relation Id",
          "type": "string"
        }
      }
    ],
    "path": "/hydrate/relation/{relation_id}",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /lookup/observations": {
    "method": "GET",
    "operationId": "lookup_observations_lookup_observations_get",
    "parameters": [
      {
        "in": "query",
        "name": "entity_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Entity Id",
          "type": "string"
        }
      },
      {
        "in": "query",
        "name": "property_query",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Property Query"
        }
      },
      {
        "in": "query",
        "name": "k",
        "required": false,
        "schema": {
          "default": 10,
          "maximum": 400,
          "minimum": 1,
          "title": "K",
          "type": "integer"
        }
      }
    ],
    "path": "/lookup/observations",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /lookup/relations": {
    "method": "GET",
    "operationId": "lookup_relations_lookup_relations_get",
    "parameters": [
      {
        "in": "query",
        "name": "subject_entity_id",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "format": "uuid",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Subject Entity Id"
        }
      },
      {
        "in": "query",
        "name": "predicate",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Predicate"
        }
      },
      {
        "in": "query",
        "name": "object_entity_id",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "format": "uuid",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Object Entity Id"
        }
      },
      {
        "in": "query",
        "name": "valid_at",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "format": "date-time",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Valid At"
        }
      },
      {
        "in": "query",
        "name": "k",
        "required": false,
        "schema": {
          "default": 50,
          "maximum": 400,
          "minimum": 1,
          "title": "K",
          "type": "integer"
        }
      }
    ],
    "path": "/lookup/relations",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /operations": {
    "method": "GET",
    "operationId": "list_operations_operations_get",
    "parameters": [],
    "path": "/operations",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "items": {
                "$ref": "#/components/schemas/ToolDescriptor"
              },
              "title": "Response List Operations Operations Get",
              "type": "array"
            }
          }
        },
        "description": "Successful Response"
      }
    }
  },
  "GET /query/saved": {
    "method": "GET",
    "operationId": "list_saved_queries_query_saved_get",
    "parameters": [
      {
        "in": "query",
        "name": "namespace",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Namespace"
        }
      },
      {
        "in": "query",
        "name": "status",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "enum": [
                "draft",
                "pending_revalidation",
                "active",
                "deprecated",
                "disabled",
                "broken"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Status"
        }
      }
    ],
    "path": "/query/saved",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "items": {
                "additionalProperties": true,
                "type": "object"
              },
              "title": "Response List Saved Queries Query Saved Get",
              "type": "array"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /query/saved/{namespace}/{name}": {
    "method": "GET",
    "operationId": "describe_saved_query_query_saved__namespace___name__get",
    "parameters": [
      {
        "in": "path",
        "name": "namespace",
        "required": true,
        "schema": {
          "title": "Namespace",
          "type": "string"
        }
      },
      {
        "in": "path",
        "name": "name",
        "required": true,
        "schema": {
          "title": "Name",
          "type": "string"
        }
      },
      {
        "in": "query",
        "name": "version",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "title": "Version"
        }
      }
    ],
    "path": "/query/saved/{namespace}/{name}",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "additionalProperties": true,
              "title": "Response Describe Saved Query Query Saved  Namespace   Name  Get",
              "type": "object"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /query/space": {
    "method": "GET",
    "operationId": "describe_query_space_query_space_get",
    "parameters": [
      {
        "in": "query",
        "name": "pattern",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Pattern"
        }
      },
      {
        "in": "query",
        "name": "include_examples",
        "required": false,
        "schema": {
          "default": false,
          "title": "Include Examples",
          "type": "boolean"
        }
      }
    ],
    "path": "/query/space",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "additionalProperties": true,
              "title": "Response Describe Query Space Query Space Get",
              "type": "object"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /query/space/search": {
    "method": "GET",
    "operationId": "search_query_space_query_space_search_get",
    "parameters": [
      {
        "in": "query",
        "name": "query",
        "required": true,
        "schema": {
          "minLength": 1,
          "title": "Query",
          "type": "string"
        }
      },
      {
        "in": "query",
        "name": "k",
        "required": false,
        "schema": {
          "default": 10,
          "maximum": 25,
          "minimum": 1,
          "title": "K",
          "type": "integer"
        }
      }
    ],
    "path": "/query/space/search",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "items": {
                "additionalProperties": true,
                "type": "object"
              },
              "title": "Response Search Query Space Query Space Search Get",
              "type": "array"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /resolve": {
    "method": "GET",
    "operationId": "resolve_resolve_get",
    "parameters": [
      {
        "in": "query",
        "name": "name",
        "required": true,
        "schema": {
          "title": "Name",
          "type": "string"
        }
      },
      {
        "in": "query",
        "name": "context_entity_ids",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "items": {
                "format": "uuid",
                "type": "string"
              },
              "maxItems": 8,
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "title": "Context Entity Ids"
        }
      }
    ],
    "path": "/resolve",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /search/chunks": {
    "method": "GET",
    "operationId": "search_chunks_search_chunks_get",
    "parameters": [
      {
        "in": "query",
        "name": "query",
        "required": true,
        "schema": {
          "title": "Query",
          "type": "string"
        }
      },
      {
        "in": "query",
        "name": "k",
        "required": false,
        "schema": {
          "default": 10,
          "maximum": 400,
          "minimum": 1,
          "title": "K",
          "type": "integer"
        }
      },
      {
        "in": "query",
        "name": "channel",
        "required": false,
        "schema": {
          "default": "semantic",
          "enum": [
            "semantic",
            "bm25"
          ],
          "title": "Channel",
          "type": "string"
        }
      }
    ],
    "path": "/search/chunks",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /search/claims": {
    "method": "GET",
    "operationId": "search_claims_search_claims_get",
    "parameters": [
      {
        "in": "query",
        "name": "query",
        "required": true,
        "schema": {
          "title": "Query",
          "type": "string"
        }
      },
      {
        "in": "query",
        "name": "k",
        "required": false,
        "schema": {
          "default": 10,
          "maximum": 400,
          "minimum": 1,
          "title": "K",
          "type": "integer"
        }
      },
      {
        "in": "query",
        "name": "channel",
        "required": false,
        "schema": {
          "default": "semantic",
          "enum": [
            "semantic",
            "bm25"
          ],
          "title": "Channel",
          "type": "string"
        }
      }
    ],
    "path": "/search/claims",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "GET /transcript/relation/{relation_id}": {
    "method": "GET",
    "operationId": "transcript_relation_transcript_relation__relation_id__get",
    "parameters": [
      {
        "in": "path",
        "name": "relation_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Relation Id",
          "type": "string"
        }
      }
    ],
    "path": "/transcript/relation/{relation_id}",
    "requestBody": null,
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /chunks/adjacent": {
    "method": "POST",
    "operationId": "post_adjacent_chunks_chunks_adjacent_post",
    "parameters": [],
    "path": "/chunks/adjacent",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/AdjacentChunksRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /connectors": {
    "method": "POST",
    "optionalProfile": "connectors",
    "path": "/connectors"
  },
  "POST /connectors/{connector_id}/pause": {
    "method": "POST",
    "optionalProfile": "connectors",
    "path": "/connectors/{connector_id}/pause"
  },
  "POST /documents/references": {
    "method": "POST",
    "operationId": "document_references_documents_references_post",
    "parameters": [],
    "path": "/documents/references",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/DocumentReferencesRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/DocumentReferencesPage"
            }
          }
        },
        "description": "Successful Response"
      },
      "400": {
        "description": "cursor is malformed or belongs to another call"
      },
      "404": {
        "description": "document_not_found | chunk_not_found"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /documents/search": {
    "method": "POST",
    "operationId": "search_documents_documents_search_post",
    "parameters": [],
    "path": "/documents/search",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/DocumentSearchRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/DocumentSearchPage"
            }
          }
        },
        "description": "Successful Response"
      },
      "400": {
        "description": "cursor is malformed"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /graph/citation-path": {
    "method": "POST",
    "operationId": "graph_citation_path_graph_citation_path_post",
    "parameters": [],
    "path": "/graph/citation-path",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/GraphCitationPathRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /graph/neighborhood": {
    "method": "POST",
    "operationId": "graph_neighborhood_graph_neighborhood_post",
    "parameters": [],
    "path": "/graph/neighborhood",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/GraphNeighborhoodRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /graph/path": {
    "method": "POST",
    "operationId": "graph_path_graph_path_post",
    "parameters": [],
    "path": "/graph/path",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/GraphPathRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /ingest": {
    "method": "POST",
    "operationId": "ingest_document_ingest_post",
    "parameters": [
      {
        "in": "query",
        "name": "filename",
        "required": true,
        "schema": {
          "minLength": 1,
          "title": "Filename",
          "type": "string"
        }
      },
      {
        "in": "query",
        "name": "mime",
        "required": true,
        "schema": {
          "minLength": 1,
          "title": "Mime",
          "type": "string"
        }
      },
      {
        "in": "query",
        "name": "title",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Title"
        }
      },
      {
        "in": "query",
        "name": "source_kind",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Source Kind"
        }
      },
      {
        "in": "query",
        "name": "source_ref",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Source Ref"
        }
      },
      {
        "in": "query",
        "name": "source_modified_at",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "format": "date-time",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Source Modified At"
        }
      },
      {
        "in": "query",
        "name": "versioning_mode",
        "required": false,
        "schema": {
          "default": "snapshot",
          "enum": [
            "snapshot",
            "living"
          ],
          "title": "Versioning Mode",
          "type": "string"
        }
      },
      {
        "in": "query",
        "name": "source_version_ref",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Source Version Ref"
        }
      },
      {
        "description": "Where the file lives at its source (a folder path or URL). Recorded with this version's metadata and observed names, so the document can later be found by it.",
        "in": "query",
        "name": "source_path",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "description": "Where the file lives at its source (a folder path or URL). Recorded with this version's metadata and observed names, so the document can later be found by it.",
          "title": "Source Path"
        }
      },
      {
        "description": "Your immutable key for this version, unique within the document (for example a publisher's edition id). A new key always creates a version; an existing key is accepted only when re-sending the latest version's bytes. Requires source_kind/source_ref.",
        "in": "query",
        "name": "version_key",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "maxLength": 512,
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "description": "Your immutable key for this version, unique within the document (for example a publisher's edition id). A new key always creates a version; an existing key is accepted only when re-sending the latest version's bytes. Requires source_kind/source_ref.",
          "title": "Version Key"
        }
      },
      {
        "description": "Start (inclusive, UTC) of the period this version's text is in force for. Requires source_kind/source_ref and versioning_mode=snapshot.",
        "in": "query",
        "name": "effective_from",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "format": "date-time",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "description": "Start (inclusive, UTC) of the period this version's text is in force for. Requires source_kind/source_ref and versioning_mode=snapshot.",
          "title": "Effective From"
        }
      },
      {
        "description": "Declared end (exclusive, UTC) of that period; without it the period lasts until the next declared start.",
        "in": "query",
        "name": "effective_until",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "format": "date-time",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "description": "Declared end (exclusive, UTC) of that period; without it the period lasts until the next declared start.",
          "title": "Effective Until"
        }
      },
      {
        "description": "One of: user | api_credential | service. Sent with X-Ingest-Principal-Ref. Ignored unless the deployment declares a trusted principal source and, where API authentication is configured, the presenting credential carries full write authority; malformed values are 422 only for a trusted assertion.",
        "in": "header",
        "name": "X-Ingest-Principal-Kind",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "description": "One of: user | api_credential | service. Sent with X-Ingest-Principal-Ref. Ignored unless the deployment declares a trusted principal source and, where API authentication is configured, the presenting credential carries full write authority; malformed values are 422 only for a trusted assertion.",
          "title": "X-Ingest-Principal-Kind"
        }
      },
      {
        "description": "Opaque caller-stable actor id, 1..255 printable ASCII characters. Sent with X-Ingest-Principal-Kind.",
        "in": "header",
        "name": "X-Ingest-Principal-Ref",
        "required": false,
        "schema": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "description": "Opaque caller-stable actor id, 1..255 printable ASCII characters. Sent with X-Ingest-Principal-Kind.",
          "title": "X-Ingest-Principal-Ref"
        }
      }
    ],
    "path": "/ingest",
    "requestBody": {
      "content": {
        "application/octet-stream": {
          "schema": {
            "contentMediaType": "application/octet-stream",
            "title": "Content",
            "type": "string"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/IngestedVersion"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /operations/{name}": {
    "method": "POST",
    "operationId": "run_operation_operations__name__post",
    "parameters": [
      {
        "in": "path",
        "name": "name",
        "required": true,
        "schema": {
          "title": "Name",
          "type": "string"
        }
      }
    ],
    "path": "/operations/{name}",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "additionalProperties": true,
            "title": "Arguments",
            "type": "object"
          }
        }
      }
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "anyOf": [
                {
                  "$ref": "#/components/schemas/Envelope"
                },
                {
                  "$ref": "#/components/schemas/ContextBundleV2"
                }
              ],
              "title": "Response Run Operation Operations  Name  Post"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /query/saved/{namespace}/{name}/run": {
    "method": "POST",
    "operationId": "run_saved_query_query_saved__namespace___name__run_post",
    "parameters": [
      {
        "in": "path",
        "name": "namespace",
        "required": true,
        "schema": {
          "title": "Namespace",
          "type": "string"
        }
      },
      {
        "in": "path",
        "name": "name",
        "required": true,
        "schema": {
          "title": "Name",
          "type": "string"
        }
      }
    ],
    "path": "/query/saved/{namespace}/{name}/run",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/RunSavedQueryRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/QueryResult"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /query/sql": {
    "method": "POST",
    "operationId": "query_sql_query_sql_post",
    "parameters": [],
    "path": "/query/sql",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/SqlQueryRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/QueryResult"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /query/sql/explain": {
    "method": "POST",
    "operationId": "explain_sql_query_sql_explain_post",
    "parameters": [],
    "path": "/query/sql/explain",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/SqlExplainRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/QueryResult"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /readiness": {
    "method": "POST",
    "operationId": "pipeline_readiness_readiness_post",
    "parameters": [],
    "path": "/readiness",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/PipelineReadinessRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/PipelineReadinessReport"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /search/chunks": {
    "method": "POST",
    "operationId": "post_search_chunks_search_chunks_post",
    "parameters": [],
    "path": "/search/chunks",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/SearchRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "POST /search/claims": {
    "method": "POST",
    "operationId": "post_search_claims_search_claims_post",
    "parameters": [],
    "path": "/search/claims",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/SearchRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/Envelope"
            }
          }
        },
        "description": "Successful Response"
      },
      "422": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/HTTPValidationError"
            }
          }
        },
        "description": "Validation Error"
      }
    }
  },
  "PUT /documents/{doc_id}/versions/{version_id}/effective-periods": {
    "method": "PUT",
    "operationId": "set_effective_periods_documents__doc_id__versions__version_id__effective_periods_put",
    "parameters": [
      {
        "in": "path",
        "name": "doc_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Doc Id",
          "type": "string"
        }
      },
      {
        "in": "path",
        "name": "version_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Version Id",
          "type": "string"
        }
      }
    ],
    "path": "/documents/{doc_id}/versions/{version_id}/effective-periods",
    "requestBody": {
      "content": {
        "application/json": {
          "schema": {
            "$ref": "#/components/schemas/EffectivePeriodsRequest"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/EffectivePeriodsSet"
            }
          }
        },
        "description": "Successful Response"
      },
      "404": {
        "description": "document_not_found | version_not_found"
      },
      "409": {
        "description": "effective_period_conflict"
      },
      "422": {
        "description": "effective_time_requires_snapshot"
      }
    }
  },
  "PUT /documents/{doc_id}/versions/{version_id}/references": {
    "method": "PUT",
    "operationId": "set_references_documents__doc_id__versions__version_id__references_put",
    "parameters": [
      {
        "in": "path",
        "name": "doc_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Doc Id",
          "type": "string"
        }
      },
      {
        "in": "path",
        "name": "version_id",
        "required": true,
        "schema": {
          "format": "uuid",
          "title": "Version Id",
          "type": "string"
        }
      }
    ],
    "path": "/documents/{doc_id}/versions/{version_id}/references",
    "requestBody": {
      "content": {
        "application/x-ndjson": {
          "schema": {
            "format": "binary",
            "type": "string"
          }
        }
      },
      "required": true
    },
    "responses": {
      "200": {
        "content": {
          "application/json": {
            "schema": {
              "$ref": "#/components/schemas/ReferencesSet"
            }
          }
        },
        "description": "Successful Response"
      },
      "404": {
        "description": "document_not_found | version_not_found"
      },
      "413": {
        "description": "reference_set_too_large (over 64 MiB)"
      },
      "422": {
        "description": "invalid_reference_set: the failing line"
      }
    }
  }
};
