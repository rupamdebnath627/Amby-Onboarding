# AMBI-AI

## BACKEND-AI-AGENTS — Conversational AI Agent Orchestration Service


### API — HTTP interface exposing all agent capabilities as REST endpoints

#### **What This Folder Does**
The `api/` folder is the HTTP surface of the entire `backend-ai-agents` system. It exposes FastAPI endpoints that wire together everything from the other three folders: orchestration (workflow execution, template rendering), agents (classifier, answer_writer, etc.), and services (search, database, platform client, context fetching).

**There are actually two separate FastAPI apps:**
* **Main app (`main.py`)** — The production API with routers for `/responses`, `/workflows`, `/executables`, `/pm`, and `/legacy`.
* **Agent API (`agent_api.py`)** — A standalone dev/debug API for calling individual agents directly.

---

#### **Architecture at a Glance**

```text
                    HTTP Request
                         │
                         ▼
              ┌──────────────────────┐
              │     FastAPI App      │  (main.py)
              │   Lifespan: init DB, │
              │   Search, Agents,    │
              │   Tools              │
              └──────┬───────────────┘
                     │
         ┌───────────┼───────────────────────┐
         │           │           │           │
         ▼           ▼           ▼           ▼
    /responses    /workflows  /pm/generate /legacy
         │           │           │           │
         ▼           ▼           ▼           ▼
    ┌─────────┐  ┌─────────┐  ┌──────┐  ┌─────────┐
    │Classifier│ │Direct   │  │PM    │  │Direct   │
    │→Workflow │ │Workflow │  │Flow  │  │Convo    │
    │→Template │ │→Template│  │→Tmpl │  │Workflow │
    └─────────┘  └─────────┘  └──────┘  └─────────┘
         │           │           │           │
         └───────────┴─────┬─────┴───────────┘
                           │
                           ▼
              ┌──────────────────────┐
              │  Translation Layer   │  (orchestration/)
              │  compile + execute   │
              │  LangGraph workflow  │
              └──────────┬───────────┘
                         │
                    ┌────┴────┐
                    ▼         ▼
              ┌─────────┐ ┌─────────┐
              │ Agents  │ │Services │
              │(execute)│ │(search, │
              │         │ │ DB, KG, │
              │         │ │ LLM)    │
              └─────────┘ └─────────┘
```

---

#### **File-by-File Breakdown**

**1. `api/__init__.py`**
Package marker. Exports nothing — routers are mounted in `main.py` directly.

**2. `api/main.py` — App Bootstrap**
The central FastAPI application factory.
* **Lifespan handler (`lifespan`):**
    * Calls `setup_memory_tables()` to ensure LangGraph checkpoint tables exist in Postgres (fail-fast if this fails).
    * Initializes `DatabaseService` and `SearchService` singletons.
    * Registers tools via `get_configured_tools()` into a global `tool_registry`.
    * Calls `register_all_agents()` to populate the agent registry with lazy factories.
    * Calls `set_legacy_dependencies()` to inject DB service into legacy routes.
    * On shutdown: closes the database connection.
* **Health endpoint (`GET /health`):** Checks database, search, LangGraph tables, and agents. Returns individual status for each plus an overall `healthy` boolean. Gracefully handles missing services (`not_configured`).
* **Router mounting:** `/workflows`, `/executables`, `/responses`, `/pm`, `/legacy`.
* **CORS:** Wide open (`allow_origins=["*"]`). Suitable for dev; needs tightening for production.

**3. `api/agent_api.py` — Standalone Agent API**
A separate FastAPI app (not mounted under main) for directly invoking any registered agent. Useful for testing and debugging.
* **Key endpoints:** `/agents` (list), `/agents/{name}` (info), `/agents/{name}/call` (execute), `/sample-contexts` (list fixtures), `/sample-contexts/{name}/call/{agent}` (execute with fixture).
* **Request model (`AgentCallRequest`):** Contains `source_context` and optional `config_overrides`.
* **Execution flow:** Validates agent, builds `ExecutionContext` and `agent_context`, calls `agent.execute(agent_context)`, returns typed output + reasoning + token usage.

**4. `api/routes/__init__.py`**
Exports the four main routers: `workflows_router`, `executables_router`, `responses_router`, `pm_router`.

**5. `api/routes/workflows.py` — Workflow Execution**
* **`POST /workflows/{workflow_id}/execute`:** Loads workflow definition, calls translation layer's `execute_workflow()`, post-processes via `render_template_values()`, stores result in memory, returns `WorkflowExecuteResponse`.
* **`GET /executions/{execution_id}`:** Retrieves stored result (lost on restart).

**6. `api/routes/executables.py` — Config Inspection**
Read-only endpoints for inspecting system config (agents, tools, workflows). Useful for admin UIs or debugging.

**7. `api/routes/responses.py` — The Main Production Endpoint**
The primary entry point where everything converges.
* **`POST /responses`:**
    * **Path 1 (Capability key provided):** Skips classifier, resolves workflow directly (or bypasses entirely for `faq_saved_answer_fetch`), executes workflow, renders template.
    * **Path 2 (Classifier path - default):** Fetches context, runs classifier to determine intent, updates context (non-fatal), resolves workflow ID, executes workflow, renders template.
    * **Message Persistence:** Validates conversation, creates user message, creates assistant placeholder (loading). On success/failure, updates placeholder status. All persistence is non-fatal.
* **`GET /logs/{execution_id}`:** Returns logs captured during workflow execution from an ephemeral in-memory store.

**8. `api/routes/responses_helpers.py` — Helper Functions**
Contains the core logic for `responses.py`.
* `run_classifier`: Runs `ClassifierAgent` and maps intent to workflow choice.
* `fetch_workflow` / `fetch_workflow_by_capability`: Resolves workflow IDs with tenant fallback.
* `execute_workflow`: Builds source context and calls translation layer.
* `persist_messages` / `complete_assistant_message` / `interrupt_assistant_message`: Manages message lifecycle (wrapped in try/except).
* `handle_capability_passthrough`: Bypass logic for direct FAQ fetches.
* `resolve_capability_resources_and_workflow`: Uses strategy pattern to fetch capability-specific resources.

**9. `api/routes/pm.py` — PM Task Endpoint**
* **`POST /pm/generate`:** Dedicated endpoint requiring `machine_id`. Fetches context, executes `pm_task` workflow, renders template, extracts PM outputs, returns `PMGenerateResponse`.

**10. `api/legacy/__init__.py` & 11. `api/legacy/experimental.py` — Legacy Routes**
Backward-compatible endpoints (predating `/responses`).
* **`POST /legacy/query`:** Creates conversation in Postgres, maps to `conversation` workflow, persists messages locally (not platform API). Simpler flow lacking classifier and capability routing.

---

#### **Key Patterns in the API Layer**

* **Non-fatal persistence:** Message creation/updates to the platform API are always wrapped in try/except. The response is returned even if persistence fails, prioritizing user experience over data completeness.
* **Two-app architecture:** Main app for production, `agent_api` for dev/debug. Keeps the testing surface separate from production routing.
* **Dual routing:** `/responses` supports both classifier-driven (automatic intent detection) and capability-key (explicit, skip-classifier) paths. The capability path is used when the frontend already knows what it needs (e.g., FAQ generation).
* **Template-driven output:** Every user-facing endpoint runs through `compose_output()` / `render_template_values()`. The API never returns raw agent output directly.
* **In-memory stores:** Execution results and logs use simple, ephemeral dictionaries (`_execution_store`, `_log_store`). These are fine for debugging but lost on restart, so they are not suitable for audit trails.

---

### Orchestration — Compiles YAML workflows into executable LangGraph state machines

#### **Architecture at a Glance**
There are two parallel execution paths in this folder:
* **Hardcoded Graph** (`pipeline.py` + `main_graph.py` + `conversation_subgraph.py`): A hand-wired LangGraph with fixed nodes and routing.
* **YAML-Driven Workflow Engine** (`translation_layer.py` + `schema_loader.py`): A dynamic system that compiles workflow definitions (loaded from a config API) into LangGraph graphs at runtime.

**Shared Supporting Modules:** `state.py`, `tool_nodes.py`, `template_builder.py`, and `history_utils.py`.

---

#### **File-by-File Breakdown**

**1. `state.py` — Shared State Definition**
Defines `AgentState`, the `TypedDict` that flows through the hardcoded graph. Key fields include:
* **Core:** `execution_id`, `playbook_name`, `user_query`
* **Session:** `conversation_id`, `thread_id`, `session_id`
* **Node Outputs:** `node_outputs` (dict keyed by node name), `agent_reasoning`, `agent_metadata`
* **Flow Control:** `decision`, `next_agent`, `requires_clarification`
* **Accumulating Fields:** `conversation_history`, `errors` (using `Annotated[List, add]` to append rather than overwrite when multiple nodes write to them)
* **Final Output:** `formatted_output`

*Helpers provided:* `create_initial_state()`, `get_agent_output()`, `set_agent_output()`.

**2. `main_graph.py` — Top-Level Routing Graph**
The main orchestration graph that decides what to do with a user query. Its own state is `MainState`.

**Flow:** `START` → `classifier` → (conditional routing) → [one of 5 paths] → `template_builder` → `END`

**Routing Logic (`route_by_intent`):**
* If `requires_clarification` is true → Routes to the **clarifier** node (ends immediately, waits for user).
* Otherwise, it routes by **intent**:
    * `"conversation"` → Conversation subgraph (the Q&A flow)
    * `"create_incident"` → Incident creator agent
    * `"pm_task"` → PM task builder agent
    * `"report"` → Report generator agent

*Note:* All paths except `clarifier` converge to `template_builder` before `END`. Agents that aren't provided get placeholder nodes that return a "not implemented" message.

**3. `conversation_subgraph.py` — The Q&A Flow**
A nested subgraph invoked when the classifier routes to `"conversation"`. Its own state is `ConversationState`.

**Flow:** `START` → [`search`, `error_lookup`, `incident_lookup`] (parallel) → `answer_writer` → `summarizer` → `followup` → `END`

* **Phase 1 — Parallel preprocessing (fan-out from START, fan-in to answer_writer):**
    * `search`: Vector store search (currently *disabled* due to RAG API timeouts).
    * `error_lookup`: Queries a Postgres knowledge graph for error/fault info, scoped by `machine_type`.
    * `incident_lookup`: Fetches incident details from a platform API if the source is a ticket/incident.
* **Phase 2 — Sequential agent chain:**
    * `answer_writer`: Generates a comprehensive answer using all gathered context (search results, error info, incident info, classifier output, conversation history).
    * `summarizer`: Creates a concise summary of the answer.
    * `followup`: Generates relevant follow-up questions.

*Note:* Each node wraps an agent's `.execute()` method, storing output in both a dedicated field (e.g., `answer_writer_output`) and in `node_outputs`.

**4. `tool_nodes.py` — Tool Instantiation & Graph Node Factories**
Handles tool configuration based on environment variables. The `get_configured_tools()` function returns a dictionary of tool instances:

| Tool | Env Var Required | Purpose |
| :--- | :--- | :--- |
| `search` (VectorStoreSearchTool) | `RAG_API_BASE_URL` or `AZURE_SEARCH_ENDPOINT` | Document search |
| `error_lookup` (ErrorLookupTool) | `POSTGRES_HOST` | Knowledge graph error lookup |
| `incident_lookup` (IncidentLookupTool) | `PLATFORM_API_BASE_URL` | Platform incident lookup |
| `web_context_enricher` (WebContextEnricherTool) | *Always attempted* | OpenAI/Gemini web search |

*Includes:* `AzureSearchToolWrapper` — an adapter that makes `SearchService` (Azure Search) compatible with the tool interface when `RAG_API_BASE_URL` isn't available.

**5. `pipeline.py` — Entry Point for the Hardcoded Graph**
The public API for executing queries through the hardcoded graph path. 
* **`execute_query()` (sync) and `execute_query_async()` (async):** The main entry points called by the API layer. They:
    * Initialize the agent registry (lazy singleton).
    * Build the full pipeline graph via `build_pipeline_graph()`.
    * Create initial state.
    * Execute the graph (optionally with a LangGraph checkpointer for conversation memory persistence).
    * Return a structured result: `{success, output, view_type, conversation_id, error}`.
* **`build_pipeline_graph()`:** Wires everything together by getting tools from `get_configured_tools()`, building the conversation subgraph, and then building the main graph with all agents from the registry.

*Note:* Memory (checkpointer) is disabled by default (`use_memory=False`).

**6. `translation_layer.py` — YAML Workflow Compiler**
This is the dynamic/config-driven execution path. It compiles `Workflow` definitions (loaded from a config API) into LangGraph graphs at runtime.
* **`compile_workflow()`:** Takes a Workflow object and:
    * Creates a `StateGraph(WorkflowState)` with reducer-based merging (supports parallel nodes).
    * Iterates over `workflow.nodes` — for each node, loads its `executable_ref` (agent or tool definition) and creates a node function.
    * Wires up edges, handling `parallel` node groups with automatic fan-out/fan-in.
    * Compiles with an optional checkpointer.
* **Node Execution (`_create_node_function`):**
    * Extracts inputs from state via `input_mapping` (JSONPath-like paths, e.g., `$.query`).
    * Executes the agent or tool.
    * Writes outputs back via `output_mapping`.
    * Stores raw output in `node_outputs` keyed by node ID.
* **Agent Execution (`_execute_agent`):**
    * Uses a factory pattern mapping executable IDs (like `"classifier"`) to factory functions.
    * Supports per-executable config: `provider`, `model`, `prompt_ref`.
    * Applies post-process transforms from the executable definition.
    * Sets/resets `tenant_id` via runtime context for multi-tenant support.
* **Tool Execution (`_execute_tool`):** Maps executable IDs to configured tools, builds appropriate args, and runs them.
* **`execute_workflow()`:** The async entry point.
    * Loads the workflow from the config API.
    * Optionally uses a Postgres-backed checkpointer for conversation memory.
    * Preloads conversation history from thread timeline via `build_history_from_thread_timeline()` if checkpointer is available.
    * Returns `{execution_id, status, result, metadata}`.

**7. `schema_loader.py` — Config API Client for Schemas**
Loads executables (agent/tool definitions) and workflows from a remote config API service:
* `load_executable(id)` → Fetches from API, parses into `Executable` model.
* `load_workflow(id)` → Fetches from API, validates as `Workflow` model.
* `list_executables()` / `list_workflows()` → Enumerates available items.

*Note:* All calls go through `get_config_client()`, supporting multi-tenancy via `tenant_id`.

**8. `template_builder.py` — Output Composition**
Assembles the final UI-facing output from agent outputs, driven by response template and layout configs fetched from the config API.
* **View type:** Determined by intent + query type (e.g., `"troubleshooting_response"`, `"procedure_response"`, `"incident_created"`).
* **Response templates:** Define components—each maps a `component_key` to a source path (e.g., `answer_writer.answer`).
* **Layouts:** Define a `layout_tree` (nested component structure) that gets hydrated with the extracted component values. Supports repeated children (e.g., lists of FAQ items, citations).

*The `build_final_output()` function is called by the `template_builder` node in the main graph.*

**9. `history_utils.py` — Conversation History Helpers**
Utilities for managing conversation history with the LangGraph checkpointer:
* **`normalize_history_messages()`:** Sanitizes arbitrary message payloads into `[{role, content}]` format.
* **`build_history_from_thread_timeline()`:** Reads checkpoint state history from the graph, extracts user/assistant turn pairs from each snapshot, deduplicates, and trims to `max_turns`.
* **`inject_history_into_source_context()`:** Merges normalized history into `source_context.source_content.conversation_history` so agents can access it.
* **`extract_answer_from_node_outputs()`:** Safely extracts the answer text from `answer_writer` output (checks both `answer` and `answer_with_markers` fields).

---

#### **Summary of Data Flow**

```text
User Query + Source Context
        │
        ▼
  ┌──────────────┐
  │  pipeline.py │  (hardcoded path)
  │     OR       │
  │  translation │  (YAML workflow path)
  │  _layer.py   │
  └──────┬───────┘
         │
         ▼
   ┌────────────┐
   │ Classifier │  ← determines intent
   └─────┬──────┘
         │
    ┌────┴─────┐
    │ Routing  │  (conversation / incident / pm_task / report / clarifier)
    └────┬─────┘
         │
         ▼ (if conversation)
   ┌────────────────────────┐
   │ Parallel Preprocessing │
   │  - search (disabled)   │
   │  - error_lookup (KG)   │
   │  - incident_lookup     │
   └────────┬───────────────┘
            │
   ┌────────▼────────┐
   │  answer_writer  │
   │  summarizer     │
   │  followup       │
   └────────┬────────┘
            │
   ┌────────▼────────┐
   │ template_builder│  ← composes UI output from config
   └────────┬────────┘
            │
            ▼
     Final Response
```


### Agents — Individual AI agent implementations, each specialized for a specific task

#### **What This Folder Does**
The `agents/` folder contains all the AI agent implementations. Each agent is an LLM-powered unit that takes context, calls an LLM (OpenAI/other providers) with a specialized prompt, and returns typed, structured output. These are the "brains" that the orchestration layer wires together into graphs.

---

#### **Architecture at a Glance**

```text
BaseAgent[T] (ABC, Generic)          AgentToolMixin
     │                                    │
     ├── ClassifierAgent                  │
     ├── SummarizerAgent                  │
     ├── FollowupAgent                    │
     ├── ClarifierAgent                   │
     ├── ReportGeneratorAgent             │
     ├── PMTaskBuilderAgent               │
     ├── AnswerWriterAgent ───────────────┘  (uses tools)
     ├── IncidentCreatorAgent ────────────┘  (uses tools)
     └── FAQGeneratorAgent ───────────────┘  (uses tools)

AgentRegistry (singleton, lazy init)
     └── register_all_agents() wires factories from config API
```

**Every agent follows the same pattern:**
* **Init:** Receives `provider`, `model`, and `prompt_ref` from the executable config (loaded from the config API).
* **Execute:** The base class calls `_execute_typed()` → returns `(TypedOutput, reasoning, tokens)`.
* **LLM call:** Uses `_call_llm()` with JSON mode → parses into a Pydantic model.

---

#### **File-by-File Breakdown**

**1. `base_agent.py` — Abstract Base Class**
The foundation for all agents. `BaseAgent[T]` is generic over `T` (a Pydantic output model extending `AgentOutputBase`).
* **LLM provider setup:** Calls `build_llm_provider(provider)` to get an adapter (OpenAI, Gemini, etc.).
* **Prompt loading:** Loads prompt config from the config API via `get_config_client().resolve_prompt(prompt_id, tenant_id)`. The prompt config contains `system_message`, optional `input_format`, and `output_format`.
* **`execute()`:** The public entry point. Wraps `_execute_typed()` with timing, logging, token tracking, and metadata assembly. Returns `(typed_output, metadata_dict)`.
* **`_call_llm()`:** Sends messages to the LLM provider, returns `(response_text, token_usage)`.
* **`_call_llm_structured()`:** Calls LLM with JSON mode, then validates the response against a Pydantic model.
* **`execute_dict()`:** Backward-compatibility wrapper that returns dicts instead of typed outputs.

**Abstract Method Requirement:**
```python
def _execute_typed(self, context, state) -> Tuple[T, str, Dict[str, int]]:
    # Returns (typed_output, reasoning_summary, token_usage)
```

**2. `registry.py` — Agent Registry (Singleton)**
A centralized, lazy-initializing registry for all agents.
* **Singleton pattern:** `AgentRegistry()` always returns the same instance.
* **Lazy factories:** Agents are registered as factory functions (`Callable[[], BaseAgent]`) and only instantiated on the first `get_agent()` call.
* **API:** `register(name, agent=..., factory=...)`, `get_agent(name)`, `list_agents()`, `has_agent(name)`.
* **Convenience functions:** `get_agent_registry()`, `register_agent()`.

**3. `tool_mixin.py` — Tool-Calling Capabilities**
A mixin class that gives agents the ability to call tools directly during execution (not just via graph preprocessing). Uses multiple inheritance: `class MyAgent(AgentToolMixin, BaseAgent[T])`. Each method checks env vars before executing (returns empty results if not configured). Tools are instantiated on-demand.

| Method | Tool | Requires | Purpose |
| :--- | :--- | :--- | :--- |
| `_call_chunk_lookup()` | `ChunkLookupTool` | Azure Search | Get raw document chunks by query |
| `_call_section_lookup()` | `SectionLookupTool` | Azure Search | Get full section content by identifier |
| `_call_error_resolver()` | `ErrorResolverTool` | Postgres KG | Get troubleshooting steps from knowledge graph |
| `_call_incident_generate()` | (Strapi) | Strapi URL | Prepare incident payload (v1: draft mode only) |
| `_call_machine_profile_lookup()`| `MachineProfileLookupTool`| Azure Search | Fetch machine profile + synthesized machine info |
| `_call_web_context_enricher()` | `WebContextEnricherTool` | OpenAI/Gemini | Generate retrieval query variants from web context |

**4. `classifier.py` — Intent Classification**
* **Purpose:** First agent in the pipeline — classifies what the user wants. The `routing_intent` is used by the orchestration layer to decide the path.
* **Input:** `user_query`, `conversation_history`, `source_content`, `machine_type`.
* **Output (`ClassifierOutput`):**
    * `query_type`: enum (`troubleshooting`, `how_to`, `factual`, `checklist`, `general`)
    * `routing_intent`: enum (`conversation`, `create_incident`, `pm_task`, `report`)
    * `intent`: descriptive string of user's goal
    * `user_context`: `UserContextInfo` (expertise level, device model, error codes, symptoms)
    * `confidence`: float (0-1)
    * `requires_clarification`: bool
    * `clarification_questions`: list

**5. `answer_writer.py` — Answer Generation with Citations**
* **Purpose:** The main answer-generation agent. Produces comprehensive answers with citation markers (e.g., `[1]`, `[2]`).
* **Tool Usage:** `chunk_lookup` (fetches additional chunks if needed) and `section_lookup` (fetches full section content). Respects an `allowed_tools` allowlist.
* **Input:** `user_query`, `classifier_output`, `retrieved_chunks`, `error_info`, `source_content`, `conversation_history`, `allowed_tools`.
* **Output (`AnswerWriterOutput`):**
    * `answer_with_markers`: full answer with citation markers
    * `answer`: convenience copy
    * `section_refs`: structured list of citation references
    * `marker_mapping`: maps markers to chunk IDs
    * `sources_used`: count
* **Notable details:** Robust JSON parsing with markdown fence handling, handles embedded JSON LLM errors, resource IDs are scoped, extensive debug logging.

**6. `summarizer.py` — Answer Summarization**
* **Purpose:** Creates a concise 2-3 sentence summary of the answer writer's output. Falls back gracefully if no answer is available.
* **Input:** `answer` or `answer_writer_output`, `classifier_output`.
* **Output (`SummarizerOutput`):** `summary` (condensed text).

**7. `followup.py` — Follow-up Question Generation**
* **Purpose:** Generates 2-3 relevant follow-up questions based on the conversation. Includes previous user questions from history to avoid repetition.
* **Input:** `user_query`, `answer` or `answer_writer_output`, `classifier_output`, `conversation_history`.
* **Output (`FollowupOutput`):** `questions` (list of strings, capped at 3).

**8. `faq_generator.py` — FAQ Generation (Most Complex Agent)**
* **Purpose:** Generates technician-oriented FAQ questions grounded in Azure Search chunks. A multi-stage pipeline within a single agent. Fail-closed design prevents hallucinated citations.
* **Pipeline Stages:**
    * **Config loading:** Reads `FAQTuning` from workflow.
    * **Scope validation:** Requires `resource_ids` in source content.
    * **Query planning:** Calls `machine_profile_lookup` and `web_context_enricher`, then uses LLM to produce retrieval queries.
    * **Chunk retrieval:** Hybrid search with semantic reranking and resource filtering.
    * **Question generation:** LLM generates questions with `supporting_ids`. Server-side validation confirms cited IDs exist.
* **Output (`FAQGeneratorOutput`):** `questions` (list of `FAQQuestionWithCitations`), `faq_metadata` (retrieval stats, fail_closed flag, reason).

**9. `pm_task_builder.py` — Preventive Maintenance Tasks**
* **Purpose:** Creates preventive maintenance task checklists for machines.
* **Input:** `user_query`, `source_content` with `machine_id` (required).
* **Execution:** Delegates to `PMFlowService` (handles breakdown → hybrid retrieval → union → fallback → markdown generation). `load_prompt=False` because it doesn't use a direct prompt file.
* **Output (`PMTaskOutput`):** `tasks` (list of `PMTaskItem`), `task_list_markdown`, `metadata`.

**10. `incident_creator.py` — Incident/Ticket Drafting**
* **Purpose:** Creates structured incident/ticket drafts from user issue descriptions.
* **Input:** `user_query`, `user_context`, `error_info`, `source_content`.
* **Output (`IncidentCreatorOutput`):** `incident_data` (title, severity, category, etc.), `draft_id`, `requires_review`, `strapi_payload`, `create_result` (draft mode only in v1).

**11. `report_generator.py` — Report Generation**
* **Purpose:** Generates structured reports from incident/task/conversation data.
* **Input:** `user_query`, `incident_info`, `conversation_history`, `task_info`, `source_content`.
* **Output (`ReportOutput`):** `report_type`, `title`, `sections` (list of `ReportSection`), `executive_summary`, `recommendations`, `data_sources`.

**12. `clarifier.py` — Clarification Questions**
* **Purpose:** Generates clarifying questions when the classifier detects ambiguity. Falls back to classifier's questions if parsing fails.
* **Input:** `user_query`, `classifier_output`, `conversation_history`.
* **Output (`ClarifierOutput`):** `questions`, `context_needed`, `suggested_options`.

**13. `__init__.py` — Package Exports & Agent Registration**
* **`register_all_agents()`:** Bootstrap function that loads executable definitions from the config API, extracts configs, and registers lazy factories.
* Currently registers: `classifier`, `answer_writer`, `summarizer`, `followup`, `faq_generator`, `pm_task_builder`. Re-exports typed output models.

---

#### **Summary: How It All Connects**

```text
Config API (executables)
        │
        ▼
register_all_agents()
        │  loads provider, model, prompt_ref per agent
        ▼
AgentRegistry (lazy factories)
        │
        ▼ (on first use)
BaseAgent.__init__()
        │  1. build_llm_provider(provider)   → OpenAI/Gemini adapter
        │  2. resolve_prompt(prompt_ref)     → system prompt from config API
        ▼
agent.execute(context)
        │  1. Calls _execute_typed()         → agent-specific logic
        │  2. _call_llm() or tool calls      → LLM / external tools
        │  3. Parse JSON → Pydantic model    → typed output
        │  4. Attach metadata (tokens, latency, reasoning)
        ▼
(TypedOutput, metadata)  → returned to orchestration graph
```

**Key Design Principles:**
* **Config-driven:** Everything (model, provider, prompts) comes from the config API, not hardcoded.
* **Typed outputs:** Every agent produces a Pydantic model, ensuring structured data flows through the graph.
* **Fail-safe tool usage:** Tools check env vars before executing and return empty results if unconfigured.
* **Multi-tenant:** Prompts and executables are resolved with `tenant_id` context.
* **Separation of concerns:** Agents own the LLM interaction; orchestration owns the graph wiring; tools own external data fetching.



### Services — External system integrations and business logic services

#### **What This Folder Does**
The `services/` folder is the infrastructure and integration layer — it contains all the clients, adapters, and service abstractions that agents and orchestration depend on to talk to external systems: LLMs, Azure Search, PostgreSQL, the platform API, and the config API. Nothing here is AI logic; it's all plumbing.

---

#### **Architecture at a Glance**

```text
┌─────────────────────────────────────────────────────────────┐
│                     External Systems                        │
│  OpenAI / Gemini │ Azure Search │ PostgreSQL │ Platform API │
└────────┬─────────────┬──────────────┬──────────────┬────────┘
         │             │              │              │
    llm_provider   search_service  database     platform_client
                   knowledge_graph              config_client
                                                context_fetcher
                                                context_service
                                                conversation
         │             │              │              │
┌────────┴─────────────┴──────────────┴──────────────┴────────┐
│             agents/ + orchestration/ + tools/               │
└─────────────────────────────────────────────────────────────┘
```

---

#### **File-by-File Breakdown**

**1. `llm_provider.py` — LLM Provider Abstraction**
The adapter layer between agents and LLM APIs. Normalizes different providers into a single interface.
* **Core interface:** Every provider has a `call()` method returning `ProviderCallResult(text, tokens)`.
* **Two providers implemented:**

| Provider | Class | SDK | Notes |
| :--- | :--- | :--- | :--- |
| **OpenAI** | `OpenAIResponsesProvider` | `openai` (Responses API) | Uses `client.responses.create()`, supports JSON mode via `text.format` |
| **Google Gemini** | `GoogleGeminiProvider` | `google-genai` | Flattens messages into a single text prompt, prepends "Return only valid JSON" for JSON mode |

* **Helper functions:**
    * `_normalize_content()`: Handles string, list-of-dicts, and nested content formats.
    * `_messages_to_text()`: Converts OpenAI-style message lists to flat `ROLE:\ncontent` text (used by Gemini).
    * `_openai_response_text()` / `_openai_tokens()`: Extract text and token counts from OpenAI's Responses API shape.
    * `_google_tokens()`: Extracts token counts from Gemini's `usage_metadata` (including `thoughts_token_count` for reasoning).
* **Factory:** `build_llm_provider(provider)` — returns the right adapter by key (`"openai"` or `"google"`).

**2. `config_client.py` — Config API Client**
A singleton client for fetching all configuration entities (workflows, agents/executables, prompts, response templates, response layouts) from an Express API.
* **Key design:** *Tenant-first fallback*. Every resolve/list call tries with a `tenant_id` parameter first, then falls back to global config (no `tenant_id`) if tenant-scoped returns nothing.
* **Available methods:**

| Method | Endpoint | Purpose |
| :--- | :--- | :--- |
| `resolve_workflow(id)` | `GET workflows/{id}` | Load workflow definition |
| `list_workflows()` | `GET workflows` | Enumerate workflows |
| `resolve_agent(id)` | `GET agents/{id}` | Load executable/agent definition |
| `list_agents()` | `GET agents` | Enumerate agents |
| `resolve_prompt(id)` | `GET prompts/{id}` | Load prompt config |
| `list_prompts()` | `GET prompts` | Enumerate prompts |
| `resolve_response_template(key)` | `GET response-templates/{key}` | Load view template |
| `list_response_templates()` | `GET response-templates` | Enumerate templates |
| `resolve_response_layout(key)` | `GET response-layouts/{key}` | Load layout definition |
| `list_response_layouts()` | `GET response-layouts` | Enumerate layouts |

*(Uses `PlatformApiFetcher` under the hood. Handles 404s by returning `None`.)*

**3. `platform_client.py` — Platform API Client**
Two classes handle platform interactions:

* **`PlatformApiFetcher` — Universal HTTP Client**
    * The low-level HTTP transport. Uses `httpx` (async and sync).
    * **Auth:** Sends `X-API-Key` header from `AMBY_EXPRESS_API_AI_SERVICE_KEY`.
    * **Error handling:** Wraps all failures in `PlatformApiFetcherError` with a `retriable` flag (true for 5xx, network errors).
* **`PlatformClient` — High-Level Platform Operations**
    * **Real methods:** `update_conversation_context()` (PATCH upsert intent/workflow), `get_ai_conversation()`, `create_ai_message()`, `update_ai_message()`, `resolve_user_resources()`, `fetch_tenant_resources()`, `get_resources_by_ids_sync()` (used by AnswerWriter for citations).
    * **Mock methods:** Placeholders for `get_user_permissions()`, `get_incident_id()`, `get_incident_details()`, `get_conversation_history()`, and `search_chunks()`.

**4. `search_service.py` — Azure AI Search**
Two search service classes targeting different indexes:
* **`SearchService` — Main Document Index** (Used by tools and FAQ generator)
    * `hybrid_search()`: Combines keyword + vector search with optional semantic reranking. Generates embeddings via OpenAI (`text-embedding-3-large`). Supports OData filters, retries without `$select` on schema mismatch, and normalizes results.
* **`PMHybridSearchService` — PM Task Index** (Used by PMFlowService)
    * `hybrid_search()`: Always filters by `machine_id` + `strategy`. Uses configurable embedding dimensions and score thresholds.
    * `union_results()`: Merges multiple result sets by doc ID (keeping the highest score).
    * `get_all_chunks()`: Fetches up to 1000 chunks for a machine/strategy.

**5. `knowledge_graph.py` — PostgreSQL Knowledge Graph**
A graph database built on PostgreSQL with `pgvector` for embeddings.
* **Upsert operations:** `upsert_node()`, `upsert_edge()`, `upsert_alias()`.
* **Lookup operations:** `get_node_by_key()`, `resolve_alias()`, `search_nodes()` (full-text search falling back from exact match), `get_out_edges()`, `get_node()`.
* **Embedding operations:** `update_node_embedding()`, `search_nodes_by_embedding()` (cosine distance search).
* **Traversal:** `traverse()` (BFS/DFS graph traversal returning ordered `TraversalStep` objects).

**6. `database.py` — Conversation Persistence (PostgreSQL)**
Direct PostgreSQL operations (separate from the knowledge graph).
* **Conversation CRUD:** `create_conversation()`, `get_conversation()`, `get_conversation_messages()`.
* **Message CRUD:** `create_message()` (stores full state including chunks, tokens, latency, reasoning), `update_message_feedback()`.

**7. `context_fetcher.py` — Request Context Assembly**
Orchestrates pre-request context gathering. `fetch_context()` runs 5 steps: User permissions, Incident details, Conversation history, Relevant chunks, and Tenant resources. 
* **Capability-specific resource fetchers:**

| Capability | Fetcher | Logic |
| :--- | :--- | :--- |
| `conversation` | `ConversationResourceFetcher` | Full scope: user + account + role + machine + asset |
| `faq_generator` / `faq_answer_generate` | `FaqResourceFetcher` | If `role_id` exists → resolve by role; else → fetch all tenant resources |
| `faq_saved_answer_fetch` | `FaqSavedAnswerFetchResourceFetcher` | Returns empty (no resource scoping needed) |

**8. `context_service.py` & 9. `conversation.py` — Context Persistence**
* **`context_service.py`:** Protocol (`ContextService`) and implementation (`ConversationContextService`) for persisting intent + workflow_id back to the platform.
* **`conversation.py`:** Minimal wrapper around a conversation ID providing `Conversation.update_context()`.

**10. `pm_flow_service.py` — PM Task Pipeline**
The full preventive maintenance retrieval + generation pipeline used by `PMTaskBuilderAgent`.
* **Pipeline (4 layers):** Breakdown (LLM query extraction) → Multi-variant retrieval (hybrid search + union) → Fallback (generic keywords/no semantic) → Optional detail enrichment → Markdown generation (LLM rendering).

**11. `runtime_context.py` — Request-Scoped Tenant ID**
Uses Python's `contextvars.ContextVar` to store a request-scoped tenant ID, accessible anywhere without explicit passing. Used extensively in `translation_layer` for multi-tenant support.

**12. `post_process_transforms.py` — Output Shaping Transforms**
A registry of functions that reshape agent outputs for UI consumption.

| Transform | Purpose |
| :--- | :--- |
| `followups_to_items` | Normalizes follow-up questions into `{label, payload}` items |
| `faq_to_list_items` | Shapes FAQ questions + citations into `{question, citations}` list items |
| `pm_tasks_to_items` | Normalizes PM tasks into `{label, payload, text}` items |
| `answer_writer_to_mixed_stream` | Builds a mixed content stream: answer text block + citation buttons from `section_refs` |

---

#### **Summary: Service Dependency Map**

```text
┌──────────────────────────────────────────────────────────────┐
│                        Config API                            │
│                    (Express backend)                         │
│  workflows │ agents │ prompts │ templates │ layouts          │
└──────────────────────┬───────────────────────────────────────┘
                       │
              config_client.py  (tenant-first fallback)
                       │
         ┌─────────────┼──────────────┐
         ▼             ▼              ▼
   BaseAgent      translation     template_builder
   (prompt        layer           (views + layouts)
    loading)      (executable
                   loading)

┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
│ llm_provider │  │search_service│  │ knowledge_graph  │
│ ──────────── │  │ ──────────── │  │ ──────────────── │
│ OpenAI       │  │ SearchService│  │ KG nodes/edges   │
│ Gemini       │  │ (main index) │  │ FTS + embeddings │
│              │  │ PMHybridSvc  │  │ BFS/DFS traverse │
│              │  │ (PM index)   │  │                  │
└──────┬───────┘  └──────┬───────┘  └────────┬─────────┘
       │                 │                   │
       ▼                 ▼                   ▼
   Agents            tool_nodes          error_lookup
   (all LLM          FAQ generator       error_resolver
    calls)           PM flow service

┌──────────────────┐  ┌────────────────┐  ┌─────────────────┐
│ platform_client  │  │ database.py    │  │ runtime_context │
│ ──────────────── │  │ ───────────    │  │ ─────────────── │
│ PlatformApiFetcher│ │ Conversations  │  │ ContextVar for  │
│ PlatformClient   │  │ Messages       │  │ tenant_id       │
│ (HTTP, auth,     │  │ Feedback       │  │ (request-scoped)│
│  retry, CRUD)    │  │                │  │                 │
└──────┬───────────┘  └────────────────┘  └─────────────────┘
       │
       ▼
  context_fetcher (assembles pre-request context)
  context_service (persists intent/workflow back)
  conversation    (wrapper for side effects)
  post_process_transforms (output shaping for UI)
```

#### **Key Design Principles**
* **Config API as single source of truth:** Prompts, models, workflows, templates, and layouts all come from the config API, not local files.
* **Multi-tenant:** Tenant-first fallback on all config lookups, utilizing request-scoped tenant IDs via `contextvars`.
* **Provider-agnostic LLM:** Agents remain oblivious to whether they're calling OpenAI or Gemini; `llm_provider.py` normalizes the interactions.
* **Fail-safe external calls:** Tools and services strictly verify environment variables before attempting connections, returning empty results if unconfigured.
* **Sync + async support:** `PlatformApiFetcher` provides both variants since certain code paths (like answer writer citation enrichment) run synchronously inside LangGraph nodes.

---

### Tools — Reusable tool implementations that agents can invoke for data retrieval

#### **What This Folder Does**
The `tools/` folder implements the tool layer — standalone, callable capabilities that agents use during execution. This is distinct from the agents themselves (which handle LLM reasoning) and the services (which are infrastructure clients). Tools act as an intermediary layer: they wrap services into validated, error-safe callable units with standardized input/output contracts.

**Two Registry Systems:**
* **`ToolRegistry`**: A simple name-to-instance map used by the orchestration layer (e.g., graph nodes like `create_search_node`).
* **`AgentToolRegistry`**: A typed registry for `BaseTool` subclasses, used by `AgentToolMixin` when agents invoke tools directly.

**Directory Structure:**
```text
tools/
├── __init__.py              # Package exports (both registries)
├── base.py                  # BaseTool, ToolResult, ToolError, ToolContext
├── registry.py              # ToolRegistry (singleton, name→Any)
├── agent_tool_registry.py   # AgentToolRegistry (typed, name→BaseTool)
├── register_builtin_tools.py# Bootstrap: registers all catalog tools
├── http_client.py           # Thin httpx wrapper for tool HTTP calls
└── catalog/
    ├── __init__.py
    ├── chunk_lookup.py       # Azure Search — raw chunk retrieval
    ├── section_lookup.py     # Azure Search — section-filtered retrieval
    ├── vector_store_search.py# External RAG API proxy
    ├── error_lookup.py       # KG — fault/error/symptom lookup
    ├── error_resolver.py     # KG — graph traversal for troubleshooting
    ├── incident_lookup.py    # Platform API — fetch incidents
    ├── incident_generate.py  # Platform API — create incidents
    ├── machine_profile_lookup.py # Azure Search + LLM — profile synthesis
    └── web_context_enricher.py   # Web search — query expansion
```

---

#### **File-by-File Breakdown**

**1. `tools/__init__.py` — Package Exports**
Exports both registry classes and their singleton accessors (`get_tool_registry()` and `get_agent_tool_registry()`).

**2. `tools/base.py` — Base Abstractions**
The foundation for all tool implementations. Defines three key types:
* **`ToolError` (Pydantic model):** `code`, `message`, `details`, `retryable`.
* **`ToolResult` (Pydantic model):** `ok` (success boolean), `data` (payload dict), `error` (ToolError), `meta` (execution metadata).
* **`ToolContext` (frozen dataclass):** `execution_id`, `agent_name`, `service_registry`.
* **`BaseTool[ArgsT]` (Generic base class):**
    * Defines `name`, `description`, `args_schema`.
    * **`invoke(raw_args, ctx)`:** Public entry point. Validates arguments, calls `self.run()`, validates return type, catches all exceptions, and wraps the output in a `ToolResult`.
    * **`run(args, ctx)`:** Abstract method subclasses override.

*Design Principle:* Tools never raise raw exceptions to agents. Every failure path returns a structured `ToolResult` with `ok=False`.

**3. `tools/registry.py` — ToolRegistry (Simple)**
A singleton pattern registry storing tools as `Dict[str, Any]` (no type enforcement). Populated during app startup in `main.py` via `get_configured_tools()`. Used by orchestration graph nodes.
Methods: `register`, `get_tool`, `list_tools`, `has_tool`, `clear`.

**4. `tools/agent_tool_registry.py` — AgentToolRegistry (Typed)**
A structured registry specifically for `BaseTool` subclasses. Used by `AgentToolMixin` when agents call tools directly.
Methods: `register`, `get`, `list`, `as_dict`.

**5. `tools/register_builtin_tools.py` — Bootstrap**
Instantiates and registers tools into the registry.
*Note:* It imports `QueryAnswerTool` from `catalog.query_answer`, which doesn't exist (causes `ImportError`). `MachineProfileLookupTool` and `WebContextEnricherTool` are instantiated on-demand, not pre-registered here.

**6. `tools/http_client.py` — HTTP Client Helper**
Thin `httpx` wrapper for tools needing simple HTTP calls (`post_json`, `get_json`). Raises `httpx.HTTPStatusError` on non-2xx.

#### **Catalog Tools**

**7. `catalog/chunk_lookup.py` — Raw Chunk Retrieval**
* **Purpose:** Queries Azure AI Search main index for raw document chunks.
* **Behavior:** Fail-closed (returns 0 chunks if `resource_ids` is explicitly empty). Builds OData filter, runs hybrid search without semantic reranking, and normalizes results. Used by AnswerWriter and FAQGenerator.

**8. `catalog/section_lookup.py` — Section-Filtered Retrieval**
* **Purpose:** Fetches chunks filtered by a specific section title.
* **Behavior:** Fail-closed on empty `resource_ids`. Builds compound OData filter (`section_title` + `resource_id`), runs hybrid search, filters for text chunks only. Used by AnswerWriter.

**9. `catalog/vector_store_search.py` — External RAG API Proxy**
* **Purpose:** Proxies queries to an external RAG API via `PlatformApiFetcher.post_sync()`.
* **Behavior:** Legacy search path (currently disabled due to timeouts).

**10. `catalog/error_lookup.py` — KG Fault/Error Lookup**
* **Purpose:** Searches the PostgreSQL knowledge graph for faults/errors/symptoms, returning related causes and actions.
* **Behavior:** Resolves machine type via Strapi. Tries vector search first (if configured), falls back to full-text search. Fetches 1-hop outgoing edges. Formats output into LLM-friendly markdown.

**11. `catalog/error_resolver.py` — KG Traversal for Troubleshooting**
* **Purpose:** Traverses the knowledge graph from a fault node to produce a troubleshooting path.
* **Modes:** `ordered_steps` (follows `next_step` chain for linear checklists) or `causes` (explores cause/action graph).
* **Behavior:** Resolves machine type, finds start node (vector-first, FTS fallback), runs BFS/DFS traversal, extracts suggested actions.

**12. `catalog/incident_lookup.py` — Fetch Incidents**
* **Purpose:** Queries the platform API (`GET /api/incidents`) for incidents filtered by machine type.

**13. `catalog/incident_generate.py` — Create Incidents**
* **Purpose:** Creates a new incident via the platform API (`POST /api/incidents`).

**14. `catalog/machine_profile_lookup.py` — Machine Profile Synthesis**
* **Purpose:** Builds a machine profile via multiple static Azure Search queries, with optional LLM synthesis.
* **Behavior:** Fail-closed on empty `resource_ids`. Runs 6 static queries, deduplicates chunks, and optionally uses an LLM to extract structured `machine_info` (using config API prompts). Used by FAQGenerator.

**15. `catalog/web_context_enricher.py` — Web Search Query Expansion**
* **Purpose:** Uses web search to expand search queries before Azure retrieval. Web content is *never* used as evidence.
* **Providers:** OpenAI (`gpt-5-mini` via Responses API + `web_search`) or Gemini (`gemini-3-flash-preview` + `Google Search` grounding). Config-driven selection.
* **Behavior:** Loads provider-specific prompts from config API, extracts web search queries from the LLM response, deduplicates, and optionally synthesizes web info summary. Used by FAQGenerator.

---

#### **How Tools Connect to the Rest of the System**

```text
┌─────────────────────────────────────────────────┐
│                   AGENTS                        │
│  (AnswerWriter, FAQGenerator, IncidentCreator)  │
│                                                 │
│  Uses AgentToolMixin to call tools directly:    │
│  _call_chunk_lookup(), _call_error_resolver()...│
└───────────────┬─────────────────────────────────┘
                │  invoke(raw_args, ctx) → ToolResult
                ▼
┌─────────────────────────────────────────────────┐
│            TOOLS (catalog/)                     │
│                                                 │
│  BaseTool.invoke() validates args, runs tool,   │
│  catches all errors → ToolResult(ok, data/error)│
│                                                 │
│  ┌──────────────┐  ┌──────────────┐             │
│  │chunk_lookup  │  │error_lookup  │  ...        │
│  │section_lookup│  │error_resolver│             │
│  └──────┬───────┘  └──────┬───────┘             │
│         │                 │                     │
└─────────┼─────────────────┼─────────────────────┘
          │                 │
          ▼                 ▼
┌─────────────────┐ ┌──────────────────┐
│  SERVICES       │ │  SERVICES        │
│  SearchService  │ │  KnowledgeGraph  │
│  (Azure Search) │ │  (PostgreSQL)    │
└─────────────────┘ └──────────────────┘
```

#### **Key Patterns**
* **Fail-closed resource filtering:** `chunk_lookup`, `section_lookup`, and `machine_profile_lookup` return empty results when `resource_ids=[]`, preventing cross-tenant data leakage.
* **Vector-first, FTS-fallback:** KG tools (`error_lookup`, `error_resolver`) prioritize embedding-based search before falling back to full-text search.
* **Config-driven LLM selection:** Tools that utilize LLMs (`machine_profile_lookup`, `web_context_enricher`) resolve their models and prompts via the config API, avoiding hardcoded values.
* **Standardized error handling:** `BaseTool.invoke()` ensures agents never see raw exceptions by wrapping all failures in a structured `ToolResult`.
* **Machine type resolution:** KG tools use `get_machine_id()` to resolve human-readable machine names to Strapi `documentIds`.

---

### FOUNDATION FILES — Configuration, models, schemas, memory, utilities

#### **What This Folder Does**
This section covers the foundational modules, declarative configurations, data models, and operational scripts that support the entire `backend-ai-agents` ecosystem. It includes the configuration facade, type system, database migrations, and the "authoring" files (YAMLs) that define how agents, prompts, and workflows behave.

---

#### **1. `config/` — Centralized Runtime Configuration**

**`config/app_config.py` — The Configuration Facade**
The single source of truth for runtime configuration. Every service, tool, and agent accesses environment variables through this module.

**Architecture (Three Layers):**
1.  **.env / process env:** Raw environment variables.
2.  **`LocalSettings`:** Uses `pydantic-settings` to auto-load `.env` into typed fields (OpenAI, Postgres, Azure Search, etc.).
3.  **`AppConfig`:** A facade with property access. Each field uses `_resolve()` logic: **Local Value First** → **Remote Provider Fallback** (e.g., KeyVault).

**UI & Layout Configs:**
* **`config/views.yaml`:** Defines Response Templates. Maps `component_key` to data source paths (e.g., `answer_writer.answer_mixed_stream`).
* **`config/layout.json`:** Defines UI Layout Trees (e.g., `tabbed_layout`, `list_layout`) used by the frontend to render the hydrated agent output.

---

#### **2. `models/` — Typed Data Models**

**`models/agent_outputs.py` — Agent Output Pipeline Type System**
Every agent produces a Pydantic model inheriting from `AgentOutputBase`. This ensures structured, predictable data flows through the LangGraph.

| Type | Agent | Key Fields |
| :--- | :--- | :--- |
| **`ClassifierOutput`** | Classifier | `query_type`, `routing_intent`, `user_context`, `requires_clarification` |
| **`AnswerWriterOutput`** | AnswerWriter | `answer_with_markers`, `section_refs` (Citations), `marker_mapping` |
| **`SummarizerOutput`** | Summarizer | `summary` |
| **`FollowupOutput`** | Followup | `questions` |
| **`PMTaskOutput`** | PMTaskBuilder | `tasks`, `task_list_markdown`, `metadata` |
| **`FAQGeneratorOutput`** | FAQGenerator | `questions` (with citations), `faq_metadata` |

---

#### **3. `schemas/` — API Request/Response Schemas**

* **`schemas/executable.py`:** Models for Agent, Tool, and Subgraph definitions. Validates provider/model settings and post-process steps.
* **`schemas/workflow.py`:** Models for Workflow definitions (nodes, edges, capability keys, and layout links).
* **`schemas/responses.py`:** The primary boundary models. Defines `CapabilityContext` (machine info, user role, etc.) and the final `ResponsesResponse`.

---

#### **4. `executables/`, `workflows/`, & `prompts/` — Declarative Definitions**

These YAML files are the "source code" for the AI's behavior. They are registered to the Config API via scripts.

* **`executables/`:** Configures specific agents (e.g., `classifier.yaml` uses Gemini) and tools (e.g., `error_lookup.yaml` points to `ErrorLookupTool`).
* **`workflows/`:** Wires nodes together.
    * `conversation.yaml`: `answer_writer` → parallel `summarizer` & `followup`.
    * `faq_generator.yaml`: Complex retrieval-heavy flow.
* **`prompts/`:** Houses all system messages.
    * `classifier.yaml`: Instructions for intent detection.
    * `pm_answer_generation.yaml`: Logic for rendering maintenance tables.

---

#### **5. `migrations/` — Database Schema SQL**

Sequential SQL scripts for PostgreSQL:
* **`001_initial_schema.sql`:** Basic tables for `conversations`, `messages`, and `pipeline_executions`.
* **`002_knowledge_graph.sql`:** Sets up `kg_nodes` with `pgvector` embeddings and `tsvector` for full-text search.
* **`003_kg_schema_migration.sql`:** Renames namespaces to `machine_type` for multi-tenant support.

---

#### **6. `memory/` — Context & Memory Services**

* **`langgraph_memory.py`:** Wraps `PostgresSaver` for thread state (checkpointer) and `PostgresStore` for long-term memory (user preferences).
* **`source_context.py`:** Unified models for where a request originates (Manual, Ticket, Checklist, API).
* **`sample_contexts.py`:** 13 predefined test fixtures used for debugging and QA.

---

#### **7. `utils/` — Structured Logging**

**`utils/logging_utils.py`**
A comprehensive logging system that tracks the lifecycle of every request using a unique `execution_id`.
* **Component Loggers:** Specialized logs for `[GRAPH]`, `[AGENT]`, `[TOOL]`, and `[MEMORY]`.
* **Safety:** Automatically filters out high-dimensional embedding vectors and base64 data from logs.
* **Capture:** `capture_execution_logs()` allows the API to return a full trace of the AI's reasoning to the frontend for debugging.

---

#### **8. `scripts/` — Operational Scripts**

| Script | Purpose |
| :--- | :--- |
| **`ingest_kg.py`** | Loads JSON fault data into the Postgres Knowledge Graph. |
| **`embed_kg.py`** | Computes OpenAI embeddings for all KG nodes for vector search. |
| **`register_from_yaml.py`** | **The Sync Tool:** Pushes local YAMLs (prompts, agents, workflows) to the Config API. |
| **`setup_demo.py`** | One-command setup: migrations → ingest KG → verify services. |

---

#### **Summary: How It All Connects**

```text
┌──────────────────────────────────────────────────────────────────┐
│                    CONFIG REGISTRATION PIPELINE                  │
│                                                                  │
│  executables/*.yaml ─┐                                           │
│  prompts/*.yaml ─────┤  register_from_yaml.py  →  Config API     │
│  workflows/*.yaml ───┤       (Express)                           │
│  config/views.yaml ──┤                                           │
│  config/layout.json ─┘                                           │
└──────────────────────────────────────────────────────────────────┘
                              │
              Config API is single source of truth
                              │
┌──────────────────────────────────────────────────────────────────┐
│                         RUNTIME                                  │
│                                                                  │
│  config/app_config.py ← .env (env vars for secrets/endpoints)    │
│                                                                  │
│  schemas/ ← validates request/response payloads at API boundary  │
│  models/ ← typed agent outputs flowing through the graph         │
│                                                                  │
│  memory/ ← LangGraph checkpointer + source context models        │
│  utils/  ← structured logging for all components                 │
│                                                                  │
│  migrations/ ← DB schema (conversations, messages, KG tables)    │
│  knowledge_graph/ ← seed data for fault/troubleshooting graphs   │
│  scripts/ ← ingest KG, embed nodes, register configs, setup demo │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  Deployment: Dockerfile → ACR → K8s (service-ai namespace)       │
│  Dev UIs: Streamlit agent_tester + workflow_demo                 │
│  Tests: pytest (unit + integration + E2E)                        │
└──────────────────────────────────────────────────────────────────┘
```

**Key Architectural Takeaway:**
The local YAML files (executables, prompts, workflows) are the **authoring format**. They are synced to the Express Config API. At runtime, the Python backend fetches these definitions from the Config API, making the system dynamic, tenant-aware, and centrally managed.

---

## BACKEND-DATA-INGESTION — Temporal-Based Document Processing Pipeline

### resource_processing_ingestion/ — Core document processing logic (non-Temporal, pure functions)

#### **What This Package Does**
This package is the document processing pipeline that takes raw uploaded files (primarily PDFs) and transforms them into searchable, indexed content in Azure AI Search. It covers every stage: document analysis, text chunking, image extraction/enrichment, embedding generation, and search index management. 

---

#### **Top-Level Files**

**`__init__.py`**
The public API surface for the package. Re-exports key functions from all submodules: Azure DI analysis, embedding generation, Azure Search indexing, index schemas, and screenshot extraction. This is what the Temporal workflow activities import.

**`resource_context.py`**
Defines a single `ResourceContext` TypedDict — the per-resource handoff contract between the ingestion orchestrator and this processing package. 
* **Fields:** `resource_id`, `resource_name`, `resource_type`, `tenant_id`, `machine_id`, `asset_id`, `file_id`, `file_url`, `container_name`. 
* *Note:* Every resource flowing through the pipeline carries this context.

**`resource_workflow_schema.py`**
The source of truth for workflow metadata persisted in the Express backend's `ingestionMetadata` field. Defines Pydantic models for every task in the pipeline:

| Model | Tracks |
| :--- | :--- |
| `DocumentIntelligenceTask` | DI result path, page/paragraph/table counts, processing time |
| `ScreenshotExtractionTask` | Screenshot path, extracted/skipped counts |
| `PMChunkingTask` | Index name, total/indexed/failed/skipped chunk counts |
| `AdhocChunkingTask` | Same structure as PM but for section-based chunks |
| `ImageProcessingTask` | Images path, total/extracted/enriched/indexed/failed counts |
| `SkuIngestionTask` | SKU index, `chunks_by_type` dict, extractor names |

The top-level `ResourceWorkflowMetadata` wraps all tasks with `pipeline_version`, `resource_id`, `run_id`, `workflow_id`, `overall_status` (completed/partial/failed/skipped), and `updated_at`. Schema versioned at 1.0 with an explicit evolution strategy.

**`resource_workflow_metadata.py`**
Builder and reader for the schemas above. Key functions:
* `build_resource_workflow_metadata(payload)`: Transforms raw dict from workflow execution to validated Pydantic model with UTC timestamp. Missing fields get safe defaults.
* `read_resource_workflow_metadata(raw_json)`: Deserializes JSON from the database back into the Pydantic model.
* `compute_overall_status(workflow_tasks)`: Derives aggregate status (e.g., all completed → "completed", mix → "partial").
* **Computed statistics:** `compute_total_chunks`, `compute_total_indexed_chunks`, `compute_chunk_success_rate`, `compute_image_success_rate`, `has_any_failures`, `get_failed_tasks`, `get_task_errors`.

**`azure_di_analyzer.py`**
Azure Document Intelligence integration.
* **Key Design:** Uses a sync SDK client in a `ThreadPoolExecutor(max_workers=4)` so the async event loop stays free for Temporal heartbeats.
* `_sync_analyze()`: Runs in a thread, creates a DI client, calls `begin_analyze_document` with the `prebuilt-layout` model and `FIGURES` output option. Blocks until done, extracts `operation_id` (needed for image extraction).
* `analyze_document_from_bytes()`: Async wrapper that submits to the thread pool and yields every 30s via `asyncio.sleep(30)` so the Temporal ActivityHeartbeat timer can fire.
* `serialize_azure_di_result()`: Handles Azure SDK object → dict conversion.

**`azure_search_indexing.py`**
Azure AI Search index management with async singleton clients:
* `get_async_search_clients(index_type)`: Returns `(AsyncSearchIndexClient, AsyncSearchClient)` singletons for "text", "images", and "sku".
* `ensure_index_exists_async()`: Creates the index if missing. Configures HNSW vector search with a `default-vector-profile`.
* `prepare_documents_for_index()`: Backwards-compat wrapper delegating to `text_schema.prepare_text_for_index`.
* `upload_documents_batch_async()`: Batch upload with per-document failure tracking.
* `close_search_clients()`: Graceful shutdown with error collection.

**`openai_embeddings.py`**
Embedding generation utilities:
* `get_embed_model_and_dim()`: Returns deployment name and dimension from centralized settings.
* `truncate_text_for_embedding()`: Uses `tiktoken` with `cl100k_base` encoding (`text-embedding-3` family). Truncates at word boundaries.
* `generate_batch_embeddings_async()`: Delegates to the centralized `LLMClient.embed()` with the configured provider.

**`resource_rename.py`**
LLM-powered resource naming that generates a human-friendly `resource_name` and `resource_description`.
* **Preview extractors:** PyMuPDF (first 2 pages), DOCX (XML parsing), PPTX (first 8 slides), CSV (first 50 rows), and plain text.
* **Logic:** Caps preview at 6000 chars, sends to Azure OpenAI with `json_object` response format.
* **Robust fallback chain:** Empty file → empty preview → LLM failure → all fall back to filename-based naming (`_fallback_result`). Max name: 120 chars. Max description: 600 chars.

---

#### **Submodules Breakdown**

**1. `index_schemas/` — Azure Search Index Definitions**
* **`__init__.py`:** Registry pattern `get_schema_config(index_type)` returns `(index_name, get_fields_fn, prepare_documents_fn)`. Uses lazy imports to avoid circular dependencies.
* **`text_schema.py`:** Defines the unified text chunks index with 24 fields. 
    * *Fields include:* Identity metadata, structural context (chapter/section), traceability (page/source ref), searchable text (max 32KB), strategy (adhoc vs pm), PM-specific data, and `text_vector` (HNSW).
    * `prepare_text_for_index()` transforms chunk dicts into index documents with safe type coercion and text truncation.
* **`images_schema.py`:** Defines the images index with 19 fields.
    * *Fields include:* Identity, location (page number), LLM enrichment (caption, detailed description, OCR, extracted entities, tags), status, and `text_vector`.
    * `prepare_images_for_index()` skips documents missing `figure_id` or embeddings.

**2. `screenshot_extractor/` — PDF Page Rendering**
* **`pdf_screenshot_renderer.py`:** Pure business logic (no Temporal awareness) using PyMuPDF (`fitz`).
    * Sets `RENDER_SCALE = 2.0` (balance of quality and size).
    * Explicitly frees pixmap memory after PNG conversion.
    * *Purpose:* Screenshots are stored in blob for UI display and passed to image enrichment LLMs as page context.

**3. `adhoc_chunking/` — Section-Based Chunking**
* **`section_chunking.py`:** Fast, rule-based chunking (no LLM needed). 
    * `extract_sections()`: Organizes DI elements by page, detects chapter boundaries, builds a hierarchical chapter → section → subsection tree. Extracts full text using parallel `ThreadPoolExecutor` workers. Sends heartbeat callbacks every 5 sections.
    * Text cleaning drops digit-only lines, normalizes labels, and collapses blank lines. Returns nested sections and subsections.

**4. `pm_chunking/` — LLM-Driven PM Task Extraction**
The intelligent chunking pipeline using LLMs to understand maintenance content semantics.
* **`pm_intelligent_chunker.py`:** * *Stages:* `extract_document_elements` → `prefilter_elements` (noise removal) → `group_elements_with_llm` (groups elements into PM chunks via LLM) → `create_chunks_from_groups` (assembles objects from verbatim text with quality gates) → `enrich_chunk_metadata` (extracts structured fields).
* **`pm_table_parser.py`:** Handles maintenance schedule tables. 
    * Two interval systems supported: Hour-based and Calendar-based.
    * *Pipeline:* `analyze_single_table` (LLM table structure analysis) → `extract_table_context` → `extract_chunks_from_table` (handles row-by-row or whole-table strategies with parallel LLM enrichment).
* **`metadata_enricher.py` & `helpers.py`:** Generates chunk IDs, source refs, cleans original text, converts LLM metadata into structured tags, and serializes extraction notes.
* **Prompts (`pm_chunking/prompts/`):**

| Prompt | Purpose |
| :--- | :--- |
| `grouping_prompt.md` | Groups document elements into complete PM chunks. Defines what PM content is/isn't. |
| `metadata_prompt.md` | Extracts structured fields from a PM chunk (task name, intervals, categories, tags). |
| `table_analysis_prompt.md` | Analyzes table structure to determine if it's a PM schedule and identifies chunking strategy. |
| `table_enrichment_prompt.md`| Enriches individual table rows with surrounding context (parts, tools, duration, warnings). |

**5. `image_processing/` — Figure Extraction and Enrichment**
* **`models.py`:** Dataclasses/Enums including `ImageType`, `ImageStatus`, `ImageTags` (LLM output), and `ImageContext`.
* **`di_image_extractor.py`:** `extract_single_image()` downloads a single figure image from Azure DI using the `operation_id` captured during document analysis.
* **`context_extractor.py`:** `extract_all_figure_contexts()` extracts bounding boxes, captions, OCR text, and surrounding text from the DI result.
* **`enrichment/` (LLM Image Tagging):** * Uses a strategy pattern with an `ImageEnricher` base class. 
    * `AzureOpenAIEnricher` (Vision API) and `GeminiEnricher` (native Part.from_bytes) routed via `get_enricher()` factory. 
* **`prompts/image_enrichment_prompt.py`:** Defines `SYSTEM_MESSAGE`, `USER_PROMPT` (instructions for 6 output fields), and `FINAL_INSTRUCTION` (JSON format specification).

---

#### **Overall Architecture Summary**

```text
Raw PDF upload
       │
       ▼
┌──────────────────────────────────────────────────────┐
│             azure_di_analyzer.py                     │
│  PDF → Azure Document Intelligence → DI result JSON  │
│  (ThreadPool so Temporal heartbeats work)            │
└──────────┬───────────┬──────────────┬────────────────┘
           │           │              │
     ┌─────▼─────┐ ┌──▼───────┐ ┌───▼──────────────┐
     │ screenshot│ │  adhoc   │ │   pm_chunking/   │
     │ _extractor│ │ chunking │ │ (LLM-driven)     │
     │           │ │ (rules)  │ │                  │
     │ PDF→PNG   │ │ sections │ │ elements→group→  │
     │ per page  │ │ →chunks  │ │ chunk→enrich     │
     └────┬───────┘ └────┬─────┘ │ + table parser   │
          │              │       └───────┬──────────┘
          │              │               │
          │         ┌────▼───────────────▼────┐
          │         │  openai_embeddings.py   │
          │         │  text → embeddings      │
          │         └────────────┬────────────┘
          │                      │
     ┌────▼──────────────────────▼─────────┐
     │        azure_search_indexing.py     │
     │  index_schemas/ (text, images, sku) │
     │  ensure index → prepare docs →      │
     │  batch upload to Azure AI Search    │
     └─────────────────────────────────────┘
           
     ┌──────────────────────────────────┐
     │    image_processing/             │
     │ DI figures → extract image bytes │
     │ → context extraction             │
     │ → LLM enrichment (OpenAI/Gemini) │
     │ → embed description → index      │
     └──────────────────────────────────┘

     ┌──────────────────────────────────┐
     │    resource_rename.py            │
     │ file preview → LLM → name/desc   │
     └──────────────────────────────────┘

     ┌──────────────────────────────────┐
     │    resource_workflow_schema.py   │
     │    resource_workflow_metadata.py │
     │ Track all task statuses & stats  │
     │ Persisted to Express backend     │
     └──────────────────────────────────┘
```

**Key Design Patterns:**
* **Two chunking strategies running in parallel:** Adhoc (fast, rule-based sections) and PM (slow, LLM-driven semantic grouping + table parsing).
* **Temporal-aware:** Functions designed as standalone activities with heartbeat support and serializable inputs/outputs.
* **Provider-agnostic:** Image enrichment works with Azure OpenAI or Gemini via factory pattern.
* **Three Azure Search indexes:** Text (unified chunks), images (enriched figures), and SKU (structured extraction).
* **Comprehensive metadata tracking:** Every task's status, counts, errors, and timing recorded in a typed Pydantic schema.

---

### temporal/ — Temporal workflow orchestration layer

#### **Overview**
This folder is the Temporal workflow orchestration layer for a document ingestion pipeline. It processes PDF documents through Azure Document Intelligence (DI), extracts text/images/structured data, chunks and enriches the content using LLMs, generates embeddings via OpenAI, and indexes everything into Azure AI Search. The system is multi-tenant, with per-tenant blob storage containers.

* **Total files:** 42 (Python + 2 shell scripts)
* **Framework:** Temporal Python SDK (`temporalio`)

---

#### **Architecture: Folder Structure**

```text
temporal/
├── __init__.py                          # Package root
├── ingestion/                           # Master orchestration workflow
├── resource_processing_ingestion/       # Per-resource processing workflow
├── chunking_ingestion/                  # PM + Adhoc text chunking
├── screenshot_extraction/               # PDF page screenshot extraction
├── image_processing/                    # Image extraction, enrichment, indexing
├── sku_ingestion/                       # Config-driven SKU extraction (error codes, etc.)
├── resource_rename/                     # LLM-based resource naming
├── sync_enriched_images/                # DB→Search index sync for image edits
└── worker/                              # Worker processes, config, and factory
    ├── config/                          # Task queues, worker configs, import registry
    ├── *_worker.py                      # 7 worker entrypoints
    ├── worker_factory.py                # Creates workers from config
    ├── logging_config.py                # Shared logging setup
    ├── start_workers.sh                 # Local multi-worker startup script
    └── worker_entrypoint.sh             # Docker/K8s single-worker entrypoint
```

---

#### **Workflow Hierarchy (Execution Flow)**

```text
IngestionWorkflow (orchestration-queue)
  │
  ├─ fetch_resources_from_express_activity
  │
  └─ ResourceProcessingWorkflow × N  (resource-io-queue, per resource, semaphore-controlled)
       │
       ├─ [Step 0] check_if_indexed_activity (idempotency check)
       │
       ├─ [Step 1, parallel]
       │    ├─ download_and_analyze_activity (Azure DI)
       │    └─ ScreenshotExtractionWorkflow (batched page→PNG)
       │
       ├─ [Step 2, parallel child workflows]
       │    ├─ PMChunkingWorkflow (pm-chunking-queue)
       │    │    ├─ prepare_pm_data_activity
       │    │    ├─ Track A: tables (analyze → extract chunks, parallel per table)
       │    │    ├─ Track B: elements (extract → group batches → create → enrich)
       │    │    ├─ finalize_pm_chunks_activity
       │    │    ├─ generate_embeddings_activity
       │    │    └─ index_to_search_activity
       │    │
       │    ├─ AdhocChunkingWorkflow (adhoc-chunking-queue)
       │    │    ├─ extract_adhoc_chunks_activity
       │    │    ├─ generate_embeddings_activity
       │    │    └─ index_to_search_activity
       │    │
       │    ├─ ImageProcessingWorkflow (image-processing-queue)
       │    │    ├─ prepare_image_extraction_activity
       │    │    ├─ extract_and_upload_image_activity × N (parallel)
       │    │    ├─ Per image: create_file_entry → enrich_with_LLM → create_enriched_record
       │    │    ├─ generate_embeddings_activity
       │    │    └─ index_to_search_activity
       │    │
       │    └─ SkuIngestionWorkflow (sku-ingestion-queue)
       │         ├─ prepare_sku_data_activity
       │         ├─ Per extractor (parallel):
       │         │    ├─ Element track: prepare → group batches → merge
       │         │    └─ Table track: filter → analyze × N → merge
       │         ├─ finalize_sku_chunks_activity
       │         ├─ generate_embeddings_activity
       │         └─ index_to_search_activity
       │
       ├─ [Step 3] cleanup_temp_files_activity (per workflow_id)
       └─ [Step 4] update_resource_workflow_metadata_activity

Standalone workflows (triggered separately, not from IngestionWorkflow):
* SingleResourceRenameWorkflow (resource-rename-queue): fetch → LLM rename → update backend
* SyncEnrichedImagesWorkflow (image-processing-queue): diff DB vs index → merge modified → index new
```

---

#### **Task Queues (7 queues)**

| Queue | Purpose | Workers |
| :--- | :--- | :--- |
| `orchestration-queue` | Light coordination, Express data fetching | 40 wf / 15 act |
| `resource-io-queue` | DI analysis, embeddings, indexing, screenshots | 15 wf / 5 act |
| `adhoc-chunking-queue` | Fast section-based text chunking | 8 wf / 8 act |
| `pm-chunking-queue` | LLM-driven PM maintenance task extraction | 8 wf / 8 act |
| `image-processing-queue` | Image extraction, LLM enrichment, sync | 8 wf / 15 act |
| `sku-ingestion-queue` | Config-driven SKU extraction (error codes, etc.) | 8 wf / 8 act |
| `resource-rename-queue` | LLM-based resource naming | 20 wf / 20 act |

---

#### **Sub-Package Analysis**

**1. `ingestion/` (3 files)**
* **Purpose:** Master orchestration - fetches resources from Express backend and spawns per-resource child workflows.
* **Key Design:** Supports scoped runs via `filter_config.scope`. Maps scope to a `sub_workflows` list passed to child workflows. Includes `fetch_resources_from_express_activity` and `IngestionWorkflow` (orchestrates with `asyncio.Semaphore` concurrency control).

**2. `resource_processing_ingestion/` (3 files)**
* **Purpose:** Per-resource orchestrator coordinating all processing for a single document.
* **Activities:** `check_if_indexed`, `download_and_analyze`, `generate_embeddings`, `index_to_search`, `cleanup_temp_files`, `update_resource_workflow_metadata`.
* **Key Design:**
    * *Idempotency:* Checks Azure Search before processing.
    * *Fault isolation:* Each child workflow failure is caught independently.
    * *Metadata tracking:* Comprehensive `ingestionMetadata` written to the resource record.
    * *Blob storage pattern:* Intermediate data passes through Azure Blob Storage to respect the Temporal 2MB payload limit.

**3. `chunking_ingestion/` (5 files)**
* **Purpose:** Two chunking strategies for text extraction.
    * **PM Chunking (LLM-driven):** 8 granular activities covering the full pipeline (data prep, table/element extraction, LLM grouping/enrichment, finalization). `PMChunkingWorkflow` runs Track A (Tables) and Track B (Elements) in parallel before merging, embedding, and indexing.
    * **Adhoc Chunking (Fast, no LLM):** Hierarchical section extraction from DI result via `AdhocChunkingWorkflow` (extract → embed → index).

**4. `screenshot_extraction/` (3 files)**
* **Purpose:** Extract PNG screenshots of every PDF page for image enrichment context.
* **Flow:** `ScreenshotExtractionWorkflow` runs `extract_screenshot_batch_activity` (processes pages in batches of 50, uses PyMuPDF, idempotent).

**5. `image_processing/` (3 files)**
* **Purpose:** Extract figures, enrich with LLM descriptions, create DB records, embed, and index.
* **Flow (`ImageProcessingWorkflow`):** Prepare (download DI, extract figure data) → Extract all images in parallel → Per image: create file, enrich, create record (sequential within) → Generate embeddings (batch) → Index to Azure Search.

**6. `sku_ingestion/` (3 files)**
* **Purpose:** Config-driven extraction of "Singular Knowledge Units" (error codes, PM tasks, warnings) using a plugin-like registry.
* **Flow (`SkuIngestionWorkflow`):** Dual-track per-extractor pipeline running an Element track and a Table track in parallel. Finalize → embed → index.

**7. `resource_rename/` (3 files)**
* **Purpose:** LLM-based resource naming.
* **Flow (`SingleResourceRenameWorkflow`):** Simple 3-step sequential pipeline: fetch, rename via LLM, update backend.

**8. `sync_enriched_images/` (3 files)**
* **Purpose:** Sync expert edits from Express DB (`enriched_images` table) back to Azure Search index.
* **Flow (`SyncEnrichedImagesWorkflow`):** Fetch parallel → diff DB vs index → sync/merge modified + index new additions.

**9. `worker/` (13 files)**
* **Purpose:** Worker process infrastructure, config registry, factory, and deployment scripts.
* **Config (`worker/config/`):** Contains `temporal_config.py`, `worker_config.py` (single source of truth for worker settings), and `import_registry.py`.
* **Worker Factory:** `create_worker()` resolves config → imports → creates Worker instance.
* **Worker Entrypoints:** Identical patterns (configure logging, load env, setup blob logger, connect Temporal, run).

| Worker | Task Queue | Special Cleanup Notes |
| :--- | :--- | :--- |
| `orchestration_worker.py` | `orchestration` | Only cleans up Express client |
| `resource_io_worker.py` | `resource-io` | Cleans up blob, Express, LLM, search clients |
| `pm_chunking_worker.py` | `pm-chunking` | Cleans up blob, Express, LLM clients |
| `adhoc_chunking_worker.py` | `adhoc-chunking` | Cleans up blob, Express clients |
| `image_processing_worker.py` | `image-processing` | Cleans up blob, Express, LLM clients |
| `sku_ingestion_worker.py` | `sku-ingestion` | Initializes extractor registry on startup |
| `resource_rename_worker.py` | `resource-rename` | Cleans up Express, LLM clients |

* **Shell Scripts:** `start_workers.sh` (Local dev background processes) and `worker_entrypoint.sh` (Docker/K8s single-worker entrypoint).

---

#### **Cross-Cutting Patterns**

| Pattern | Implementation |
| :--- | :--- |
| **Payload Size Management** | All large data (DI results, chunks, embeddings) stored in Azure Blob Storage; paths are passed as payloads. |
| **Heartbeats** | Long-running activities use `start_activity_heartbeat()` with progress updates. Timeouts configured (2-20 min). |
| **Memory Management** | Aggressive `del` + `gc.collect()` after large data. CPU-bound work offloaded to threads via `asyncio.to_thread()`. |
| **Concurrency Control** | `asyncio.Semaphore` at workflow level for parallel child workflows/activities. Limits from `settings`. |
| **Fault Tolerance** | `asyncio.gather(return_exceptions=True)` everywhere. Failures isolated per resource/image/table/batch. |
| **Idempotency** | Pre-checks (`check_if_indexed_activity`), screenshot `blob_exists` checks. Targeted re-runs bypass idempotency. |
| **Retry Policies** | All activities have a `RetryPolicy` (typically 2-5 attempts, exponential backoff). |
| **Workflow Metadata** | `update_resource_workflow_metadata_activity` persists execution state to Express DB for monitoring. |
| **Sandbox Compliance** | Imports use `workflow.unsafe.imports_passed_through()`. Activity imports deferred to avoid SDK triggers at module level. |

---

#### **External Dependencies**

* **Azure Document Intelligence** - PDF analysis
* **Azure Blob Storage** - Intermediate and permanent file storage
* **Azure AI Search** - Vector search index (text, images, SKU)
* **OpenAI** - Embeddings (`generate_batch_embeddings_async`)
* **LLM Provider** - Configurable (`settings.llm_provider`) for chunking, enrichment, table analysis, resource naming
* **Express Backend** - REST API via `DbInterface` (Resource CRUD, file management)
* **Temporal Server** - Workflow orchestration
* **PyMuPDF** - PDF page rendering for screenshots

---

#### **Summary Statistics**

| Metric | Count | Details |
| :--- | :--- | :--- |
| **Workflows** | 9 | `IngestionWorkflow`, `ResourceProcessingWorkflow`, `PMChunkingWorkflow`, `AdhocChunkingWorkflow`, `ScreenshotExtractionWorkflow`, `ImageProcessingWorkflow`, `SkuIngestionWorkflow`, `SingleResourceRenameWorkflow`, `SyncEnrichedImagesWorkflow` |
| **Activities** | ~40 | Unique activity functions |
| **Task Queues** | 7 | |
| **Worker Types** | 7 | |
| **Python Files** | 40 | |
| **Shell Scripts** | 2 | |

---

### unified_sku_ingestion/ — Config-driven structured data extraction framework

#### **What This Folder Does**
This is a config-driven, LLM-powered knowledge extraction system that processes technical documents (manuals, service guides) into singular, searchable knowledge units (SKU chunks) stored in Azure AI Search.

#### **1. Directory Structure**

```text
unified_sku_ingestion/
├── config/
│   ├── __init__.py
│   ├── sku_config.py                # Dataclass definitions & YAML loaders
│   ├── extractor_registry.py        # Dynamic extractor registration (singleton + decorator)
│   ├── prompt_generator.py          # Auto-generate LLM prompts from config
│   ├── pattern_matcher.py           # Config-driven regex/keyword tag extraction
│   ├── keyword_expander.py          # LLM-powered keyword expansion (one-time, cached)
│   ├── config_validator.py          # User-friendly config validation
│   ├── index_schema.py              # Azure AI Search index schema (40 fields)
│   └── extractors/
│       ├── TEMPLATE.yaml            # Copy-paste template for new extractors
│       ├── error_code.yaml          # Error code extractor config
│       └── pm_task.yaml             # PM task extractor config
├── extractors/
│   ├── __init__.py
│   ├── base_sku_extractor.py        # Abstract base class (~1150 lines)
│   ├── error_code_extractor.py      # Error code extraction (~487 lines)
│   └── pm_task_extractor.py         # PM task extraction (~367 lines)
├── prompts/
│   ├── error_extraction.md          # Static fallback prompt
│   └── table_analysis.md            # Table analysis prompt
├── enrichment/
│   └── __init__.py                  # (Placeholder)
├── scripts/
│   ├── validate_config.py           # CLI config validator
│   └── expand_keywords.py           # Keyword expansion utility
├── tests/
├── docs/
└── README.md
```

#### **2. File-by-File Breakdown**

**Config Layer**

| File | Lines | Purpose |
| :--- | :--- | :--- |
| `sku_config.py` | ~582 | Master config: `UnitExtractorConfig` (40+ fields), nested policies (`ContextPolicy`, `TagPolicy`, `TableHandlingPolicy`, `ContentDetectionPolicy`, `KeywordExpansionPolicy`, `PromptGenerationPolicy`). Loads/saves YAML. |
| `extractor_registry.py` | ~198 | Singleton `ExtractorRegistry` with `@register_extractor("type")` decorator. Dynamically discovers and instantiates extractors from YAML configs. |
| `prompt_generator.py` | ~369 | `PromptGenerator` builds LLM prompts dynamically from config (goal, rules, quality, output format sections). Replaces static `.md` files. |
| `pattern_matcher.py` | ~195 | `PatternMatcher` extracts tags via regex patterns, keyword lists, or categorized keywords. Supports normalization rules. |
| `keyword_expander.py` | ~254 | `KeywordExpander` uses LLM to expand seed keywords (synonyms, abbreviations, technical terms). One-time expansion, cached in config. |
| `config_validator.py` | ~273 | `ConfigValidator` validates all config sections with user-friendly error messages and actionable fix suggestions. |
| `index_schema.py` | ~783 | Defines 40 Azure AI Search fields. `get_unified_sku_index_fields()` returns schema; `prepare_sku_for_index()` transforms chunks to search documents. |

**Extractor Layer**

| File | Lines | Purpose |
| :--- | :--- | :--- |
| `base_sku_extractor.py` | ~1150 | Abstract base class. Defines `DocumentElement`, `SkuChunk`, and the full extraction pipeline (10 steps). Subclasses override `load_prompt_template()`, `parse_llm_response()`, and optional hooks. |
| `error_code_extractor.py` | ~487 | `@register_extractor("error_code")`. Extracts error codes, severity, categories, affected systems, resolution steps. Normalizes codes (e.g., "alarm 203" -> "ALARM-203"). |
| `pm_task_extractor.py` | ~367 | `@register_extractor("pm_task")`. Extracts PM tasks, intervals, components, procedures, safety notes. Normalizes intervals (e.g., "500 hours" -> "500_hours"). |

**YAML Configs**

| File | Purpose |
| :--- | :--- |
| `TEMPLATE.yaml` | 337-line annotated template. All fields marked `[REQUIRED]`/`[OPTIONAL]`. |
| `error_code.yaml` | Inclusion keywords (error, alarm, fault, etc.), exclusion patterns, required fields, tag rules, keyword expansion enabled. |
| `pm_task.yaml` | Inclusion keywords (maintenance, inspect, replace, etc.), larger context windows (500 chars), interval/component tag extraction. |

#### **3. Architecture & Data Flow**

The system uses a multi-layer content detection pipeline:

```text
Document (from Azure Document Intelligence)
  │
  ▼
┌──────────────────────────────────────────┐
│ Layer 1: Keyword Prefiltering (FREE)     │  Inclusion keywords + exclusion patterns
│   ~50-70% of elements removed            │  + length limits
└──────────────────┬───────────────────────┘
                   ▼
┌──────────────────────────────────────────┐
│ Layer 2: Pattern Matching (FREE)         │  Optional regex-based filtering
└──────────────────┬───────────────────────┘
                   ▼
┌──────────────────────────────────────────┐
│ Layer 3: LLM Relevance Check ($$)        │  Optional per-element validation
│   Batched (10/call), confidence >= 0.7   │  Disabled by default in both configs
└──────────────────┬───────────────────────┘
                   ▼
┌──────────────────────────────────────────┐
│ Core LLM Extraction                      │  Groups elements into SKU units
│   Auto-generated or static prompt        │  Returns JSON with fields + confidence
└──────────────────┬───────────────────────┘
                   ▼
┌──────────────────────────────────────────┐
│ Table Extraction (parallel)              │  LLM analyzes table structure first
│   Column mapping → row-by-row chunks     │  then extracts per-row chunks
└──────────────────┬───────────────────────┘
                   ▼
┌──────────────────────────────────────────┐
│ Enrichment                               │
│  • Context: section titles + surrounding │
│    text (configurable char windows)      │
│  • Tags: deterministic rules + patterns  │
│    + metadata-derived                    │
└──────────────────┬───────────────────────┘
                   ▼
┌──────────────────────────────────────────┐
│ prepare_sku_for_index()                  │  40-field Azure Search documents
│   + text-embedding-3-large (3072 dims)   │  ready for upload
└──────────────────────────────────────────┘
```

#### **4. External Dependencies & Integrations**

| Service | Usage |
| :--- | :--- |
| Azure Document Intelligence | Source of document structure (pages, paragraphs, sections, tables) |
| Azure OpenAI | LLM calls for relevance checking, element grouping, table analysis, context filtering, keyword expansion |
| Azure AI Search | Target index for storing enriched chunks with vector embeddings |
| `shared_utilities.ai_provider_client.LLMClient` | Centralized LLM client abstraction |

#### **5. Key Design Patterns**

| Pattern | Where | Purpose |
| :--- | :--- | :--- |
| Registry + Decorator | `extractor_registry.py` | `@register_extractor("error_code")` for zero-config discovery |
| Template Method | `base_sku_extractor.py` | Base defines pipeline; subclasses override hooks |
| Strategy | `pattern_matcher.py`, `keyword_expander.py` | Pluggable extraction/expansion strategies |
| Dataclass Config | `sku_config.py` | Immutable, serializable configuration with nested policies |
| Lazy Import | `base_sku_extractor.py` | Optional modules (`PromptGenerator`, `PatternMatcher`) degrade gracefully |
| Graceful Degradation | Throughout | LLM failures -> fail open (include all elements) or use fallbacks |

#### **6. Key Metrics & Thresholds**

| Parameter | Default | Notes |
| :--- | :--- | :--- |
| Min confidence | `0.6` | Varies by extractor |
| Min content length | `50` chars | Filters noise |
| Max content length | `5000` chars | Prevents runaway extraction |
| Context window | `500` chars before/after | PM tasks; error codes use smaller |
| Max context | `2000` chars | Hard cap |
| Elements per batch | `50` | LLM batch size |
| Tag max length | `25` chars | Normalized |
| Embedding dimensions| `3072` | `text-embedding-3-large` |
| Reported accuracy | `95%` | With LLM layers enabled |
| Cost per doc | `~$2.40` | vs `$16.50` in prior flow (85% savings) |

#### **7. Notable Concerns**

* **Async orchestration:** Methods like `group_elements_with_llm()` and `extract_from_tables()` are async but the orchestrator isn't here. They are likely called from Temporal workflows elsewhere.
* **Config/file mismatch:** The PM config references `pm_grouping_prompt.md`, which doesn't exist. It falls back to auto-generation, but could confuse developers.
* **Missing image extraction:** The index schema has full image support, but there is no image extraction code here. Handled elsewhere.
* **No LLM call caching/deduplication:** Each batch is a separate LLM call with no request-level caching (except for keyword expansion).
* **Simple sentence splitting:** Context extraction uses a basic regex `r'[.!?]\s+'`, which fails on abbreviations (e.g., "Dr. Smith").
* **Thread safety:** The global singleton registry lacks locks. It's fine for async/Temporal but unsafe for threaded environments.
* **`enrichment/` module:** Contains only `__init__.py`, indicating planned but unimplemented features.

#### **Summary**

This is a well-architected, production-grade extraction system following a "config over code" philosophy. Adding a new content type (e.g., warnings, specifications) requires copying `TEMPLATE.yaml`, editing it (~30 min), and optionally writing a subclass. The multi-layer filtering design offers fine-grained cost/quality tradeoffs, and the 40-field Azure Search schema supports rich retrieval across text, tables, images, and metadata.

---

### utilities/ — Cross-cutting infrastructure utilities

This folder is the shared utility layer for the backend data ingestion service — a Temporal-based workflow system that processes documents (PDFs, images) through Azure services for a multi-tenant SaaS platform.

#### **Folder Overview**

| File | Size | Purpose |
| :--- | :--- | :--- |
| `__init__.py` | 830B | Public API re-exports |
| `llm_client.py` | 877B | LLM client singleton |
| `temporal_client.py` | 1.8KB | Temporal server connection |
| `blob_sas_utils.py` | 7.2KB | Azure Blob SAS token generation |
| `helpers.py` | 9.3KB | Pure helpers (IDs, timing, heartbeats) |
| `worker_blob_logger.py` | 9.3KB | Worker log upload to Azure Blob |
| `db_interface.py` | 15.4KB | Database abstraction layer (Express API) |
| `workflow_blob_storage.py` | 22.7KB | Workflow intermediate file storage |

---

#### **File-by-File Analysis**

**1. `__init__.py`**
The package's public API. Re-exports selected symbols from `helpers`, `blob_sas_utils`, and `workflow_blob_storage` via `__all__`. Notably does not re-export `llm_client`, `temporal_client`, `db_interface`, or `worker_blob_logger` — those are imported directly by consumers.

**2. `llm_client.py` — LLM Client Singleton**
Wraps `shared_utilities.ai_provider_client.LLMClient` as a module-level singleton.
* `get_llm_client()` — Lazy-creates the singleton; uses env-var API keys.
* `close_llm_client()` — Async teardown, resets the singleton to `None`.
* *Note:* Very thin (27 lines) — all provider logic lives in the shared library.

**3. `temporal_client.py` — Temporal Connection**
* `get_temporal_settings()` — Reads `temporal_host` and `temporal_namespace` from centralized `config.settings`.
* `connect_temporal_client(host, namespace)` — Connects to Temporal with optional overrides.
* `get_temporal_client()` — Convenience alias using defaults.
* `terminate_all_running_workflows(query, reason)` — Bulk-terminates running workflows matching a query string. Continues on per-workflow errors (silently swallows exceptions).

**4. `blob_sas_utils.py` — SAS Token Generation**
Provides URL-based access to Azure blobs for Azure Document Intelligence (no file download needed).
* `BlobUrlParts` (dataclass) — Parsed Azure blob URL components: `account_name`, `container_name`, `blob_path`, `scheme`. Has `to_base_url()` to reconstruct without query params.
* `parse_blob_url(url)` — Strict parser for `https://{account}.blob.core.windows.net/{container}/{blob}` format. Validates scheme, hostname, path structure.
* `extract_account_key_from_connection_string(conn_str)` — Parses the semicolon-delimited connection string to extract `AccountKey`.
* `generate_blob_sas_url(...)` — Generates a short-lived SAS URL. Lazy-imports `azure.storage.blob` to avoid import-time SDK dependency. Forces HTTPS protocol.
* `validate_blob_url(url)` / `is_azure_blob_url(url)` — Validation helpers (full parse vs. quick hostname check).

**5. `helpers.py` — Pure Helpers**
A mixed bag of utilities split into three logical groups:
* **ID & Formatting:**
    * `generate_temporal_workflow_run_id()` — Generates `run-YYYYMMDD-HHMMSS` IDs (uses deprecated `datetime.utcnow()`).
    * `sanitize_for_workflow_id(text)` — Lowercases, replaces non-alphanum with hyphens, truncates to 50 chars.
    * `sanitize_for_path(text)` — Replaces special chars with underscores.
    * `short_list(items, max_items=10)` — Truncated list display with `...(+N more)` suffix.
    * `format_exception_for_log(e)` — Produces `ExcType: message` strings.
* **Environment:**
    * `load_ingestion_env(dotenv_path)` — Loads `.env` from the package root via `python-dotenv`.
* **Timing:**
    * `timing_start(...)` / `timing_end(...)` — Print-based structured timing logs using `time.perf_counter()`. Formats key=value pairs sorted alphabetically.
* **Temporal Heartbeats:**
    * `parse_utc_timestamp(ts_value)` — Parses ISO 8601 strings or datetime objects into UTC-aware datetimes.
    * `ActivityHeartbeat` class — Manages periodic Temporal activity heartbeats. Spawns an `asyncio.Task` that calls `activity.heartbeat()` every 30s with a dict payload. Includes `update()`, `callback()`, and `stop()` methods.
    * `start_activity_heartbeat(...)` — Factory function.

**6. `worker_blob_logger.py` — Worker Log Uploader**
Uploads local worker log files to Azure Blob Storage as append blobs, then truncates local files.
* **Lazy Azure imports** (`_ensure_azure_imports()`) — Avoids Temporal workflow determinism issues by deferring `azure.storage.blob` import.
* **`WorkerBlobLogger` class:**
    * Singleton per worker name via `__new__` + class-level `_instances` dict.
    * Log file path: `backend_data_ingestion/logs/workers/{worker_name}.log` (works in K8s and local).
    * `start_periodic_upload(interval_seconds=60)` — Spawns a daemon thread that uploads every N seconds.
    * `upload_to_blob()` — Reads local file, appends to Azure append blob, truncates local file. Handles blob type mismatches. Uses thread lock for safety.
    * `stop()` — Sets stop flag, does final upload, joins thread.
* **Module-level helpers:** `setup_worker_blob_logger(name, interval)` and `stop_all_worker_loggers()`.

**7. `db_interface.py` — Database Abstraction**
Protocol-based abstraction over the Express backend API.
* **`DbInterface` (Protocol)** — Defines the contract: generic CRUD (`get_resource()`, `update_resource()`), workflow metadata (`read/write_resource_workflow_state()`), and file/image record management.
* **`_ExpressDbInterface` (Concrete implementation):**
    * Wraps `shared_utilities.express_client.ExpressClient`, scoped to a `tenant_id`.
    * All API calls are tenant-scoped (tenant UUID in URL path).
    * Workflow state uses `ResourceWorkflowMetadata` Pydantic model, stored in `ingestionMetadata` JSONB column.
* **Singleton pattern via `get_db_interface(tenant_id)`:** First call must provide `tenant_id`. Subsequent calls reuse the instance. Passing a different `tenant_id` recreates it and schedules an async close of the old client to avoid leaks.
* `close_express_client()` — Async cleanup for worker shutdown.

**8. `workflow_blob_storage.py` — Workflow File Storage**
The largest and most complex module. Manages intermediate and permanent files in Azure Blob Storage for multi-pod workflows.
* **`ResourceBlobPaths`** — Path constants and builders (e.g., `{resource_id}/DI_Result/`, `{resource_id}/Screenshots/`, `{resource_id}/Images/`).
* **Lazy Azure imports** — Uses async SDK (`azure.storage.blob.aio`).
* **`WorkflowBlobStorage` class:**
    * Persistent `BlobServiceClient` with connection pooling.
    * Path scheme: `{prefix}/{folder_path}/{filename}`.
    * **Downloads:** `download_json()` (async chunked download + off-thread JSON parse), `download_bytes()`.
    * **Uploads:** `upload_json()` and `upload_to_blob_path()`. Auto-creates containers. Large files (>10MB) use block upload (`_upload_large_blob()`).
    * **Utilities:** `blob_exists()`, `delete_workflow_files()`, `is_blob_path()`, `close()`.
* **Multi-tenant instance cache** (`_instances`):
    * `init_blob_storage(container)` — Creates and caches per-container instances.
    * `get_blob_storage(container)` — Returns cached instance, auto-initializes if needed.
    * `close_all_blob_storage()` — Async teardown of all instances.

---

#### **Cross-Cutting Patterns**

| Pattern | Where Used |
| :--- | :--- |
| **Singleton / Module-level cache** | `llm_client.py`, `db_interface.py`, `workflow_blob_storage.py`, `worker_blob_logger.py` |
| **Lazy Azure SDK imports** | `blob_sas_utils.py`, `worker_blob_logger.py`, `workflow_blob_storage.py` (avoids Temporal determinism violations) |
| **Centralized config** | All modules read from `backend_data_ingestion.config.settings` |
| **Async-first design** | `db_interface.py`, `workflow_blob_storage.py`, `temporal_client.py`, `llm_client.py` (close) |
| **Tenant scoping** | `db_interface.py` (tenant in URL), `workflow_blob_storage.py` (tenant container) |
| **Heartbeat integration** | `helpers.py` (`ActivityHeartbeat`), `workflow_blob_storage.py` (callbacks) |
| **Graceful shutdown** | `close_llm_client()`, `close_express_client()`, `close_all_blob_storage()`, `stop_all_worker_loggers()` |

---

#### **Dependencies**

* **Azure SDK:** `azure.storage.blob` (sync + async), `azure.core.exceptions`
* **Temporal:** `temporalio.client`, `temporalio.activity`
* **Internal shared libs:** `shared_utilities.ai_provider_client`, `shared_utilities.express_client`
* **Internal config:** `backend_data_ingestion.config.settings`
* **Internal schemas:** `resource_workflow_schema.ResourceWorkflowMetadata`
* **Standard lib:** `asyncio`, `json`, `logging`, `threading`, `time`, `re`, `dataclasses`, `pathlib`, `urllib.parse`
* **Third-party:** `python-dotenv`

---

### Root Files

#### **Overview**
This folder is the root of a Temporal-based data ingestion workflow service (version `1.54.0`) built with Python. It processes machine documentation through dual pipelines — AI provider vector stores (OpenAI/Gemini) and Azure AI Search — using FastAPI as the API layer and Temporal for distributed, durable workflow orchestration.

---

#### **File-by-File Analysis**

**1. `config.py` — Centralized Configuration (101 lines)**
Uses Pydantic `BaseSettings` to load all configuration from environment variables (with `.env` fallback). Defines the `IngestionSettings` class with ~40 settings grouped into:
* **Azure OpenAI:** Endpoint, API key, deployment names (grouping, metadata, light, embedding).
* **Azure Document Intelligence:** Endpoint, key, model type (`prebuilt-layout`).
* **Azure AI Search:** Endpoint, key, 3 index names (unified chunks, images, SKU), embedding dimension (3072), batch size.
* **Embedding:** Batch size (500), max tokens (8000).
* **Temporal:** Host and namespace (required).
* **Express Backend:** API URL and service key for the Node.js backend.
* **Chunking Limits:** PM batch sizes/confidence thresholds, Adhoc section processing worker count.
* **Orchestration:** Max concurrent resource workflows (default 20).
* **Multi-provider AI keys:** OpenAI, Google, OpenRouter, Anthropic, xAI.
* **LLM Provider Selection:** Switchable via `llm_provider` field (default `openai`).
* **Image Enrichment:** Configurable provider/model (default `gemini` / `gemini-2.5-flash`).
* **Azure Blob Storage:** Connection strings for logs, intermediate files, and source documents.
*Instantiates a global `settings` singleton at the module level.*

**2. `ingestion_api.py` — FastAPI Application (523 lines)**
The main API server. Key endpoints:

| Endpoint | Method | Purpose |
| :--- | :--- | :--- |
| `/` | `GET` | API info and endpoint listing |
| `/health` | `GET` | Health check (API + Temporal connectivity) |
| `/ingestion/start` | `POST` | Start ingestion workflows (returns 202) |
| `/ingestion/status/{run_id}` | `GET` | Poll workflow status with progress summary |
| `/ingestion/terminate-all-workflows` | `POST` | Background-terminate all running workflows |
| `/ingestion/termination-status` | `GET` | Poll termination progress |
| `/ingestion/resources/rename` | `POST` | Fire-and-forget rename workflows per resource |
| `/ingestion/sync-enriched-images` | `POST` | Sync expert image edits to Azure Search |

* **Key details:**
    * Lifespan management connects to Temporal on startup; gracefully degrades if unavailable.
    * `IngestionRequest` supports multiple scopes (`all`, `resource-workflow`, `pm-chunking`, etc.) with filters.
    * `ResourceRenameRequest` fires one `SingleResourceRenameWorkflow` per unique ID.
    * Uses in-memory dicts (`run_status`, `termination_status`) for state tracking (not persisted across restarts).
    * Status polling uses a 1-second `asyncio.wait_for` to distinguish "running" from "completed".

**3. `Dockerfile` — Monolithic Container (33 lines)**
* Base: `python:3.11-slim`
* Installs `gcc` for native extensions.
* Copies full dependencies (`requirements.txt`), `backend-data-ingestion/`, and `shared-utilities/`.
* Runs both workers and API in a single container (`start_workers.sh &` + `uvicorn`). Exposes port `8001`.

**4. `Dockerfile.api` — API-Only Container (24 lines)**
* Lightweight: installs only `requirements-api.txt` (excludes heavy Azure SDK dependencies).
* Includes a `HEALTHCHECK` pinging `/health` every 30s.
* Runs Uvicorn server on port `8001`.

**5. `Dockerfile.worker` — Worker-Only Container (36 lines)**
* Full dependencies plus `gcc` and `procps`.
* Accepts a `WORKER_TYPE` environment variable to select which worker to run via `worker_entrypoint.sh`.
* `HEALTHCHECK` checks for a running Python process (communicates via Temporal gRPC, no HTTP).

**6. Deployment Scripts (Shell)**

| Script | Target | Architecture | Notes |
| :--- | :--- | :--- | :--- |
| `deploy.sh` | Local Docker | Monolithic | `localhost:8001`, no registry |
| `deploy-docker.sh` | Remote VM | Monolithic | Target IP: `20.236.255.137`, mounts `logs/` volume |
| `deploy-aks.sh` | Dev/QA AKS | Split (9 deployments) | 1 API + 8 Worker deployments, SSL via cert-manager |
| `deploy-aks-prd.sh`| Prod AKS | Monolithic | Single deployment, SSL via cert-manager |

**7. Miscellaneous Files**
* **`__init__.py`:** Package declaration exporting core modules.
* **`pyproject.toml`:** Tracks project version (`1.54.0`) for CI tagging.
* **`requirements.txt`:** Full worker dependencies (20 packages including `temporalio`, `fastapi`, `azure-ai-documentintelligence`, `PyMuPDF`).
* **`requirements-api.txt`:** Lean API dependencies (8 packages, excludes Azure SDKs/PyMuPDF).
* **`README.md`:** Comprehensive documentation (architecture diagrams, quick start, API reference, Temporal glossary).

---

#### **Key Architectural Observations**

* **Dev vs. Prod Divergence:** Dev/QA uses a split microservice architecture (API + 8 separate worker deployments), while Production uses a monolithic single-container deployment.
* **Multi-Provider LLM Support:** Switchable between OpenAI, Azure OpenAI, Google Gemini, Anthropic, xAI, and OpenRouter via the `LLM_PROVIDER` env var.
* **Ephemeral In-Memory State:** Tracking dictionaries in the API (`run_status`, `termination_status`) are lost on restart. Temporal is the true source of state.
* **Containerization Strategy:** Uses 3 Dockerfiles (Monolithic, API-only, Worker-only) to support both simple VM deployments and scalable Kubernetes clusters.
* **Shared Utilities Coupling:** All Dockerfiles copy `shared-utilities/` from the parent directory, indicating a cross-package dependency.

---


## SHARED-UTILITIES — FILE INTERCONNECTION MAP

This is a Python package providing shared infrastructure clients used across the Amby AI platform. It contains 20 files across 3 directories, organized into two main concerns:
* **External API clients** (HTTP, Express backend, Strapi CMS)
* **LLM provider abstraction layer** (6 AI providers behind a unified interface)

---

### **Directory Structure**

```text
shared-utilities/
├── __init__.py                          # Package marker (empty docstring)
├── http_client.py                       # Generic async HTTP download client
├── express_client.py                    # Express backend API client
├── strapi_client.py                     # Strapi CMS API client
└── ai_provider_client/                  # LLM provider abstraction
    ├── __init__.py                      # Public API exports
    ├── README.md                        # Comprehensive documentation
    ├── ai_params.py                     # Parameter builder for provider quirks
    ├── base_provider.py                 # Abstract base class
    ├── client.py                        # Main routing layer (LLMClient)
    ├── dot_dict.py                      # Dict with dot-notation access
    ├── exceptions.py                    # Unified LLMProviderError
    ├── key_pool_client.py               # Dynamic API key pool client
    └── providers/                       # Individual provider implementations
        ├── __init__.py                  # Lazy-load registry
        ├── anthropic.py                 # Anthropic (Claude)
        ├── azure_openai.py              # Azure OpenAI
        ├── google.py                    # Google Gemini
        ├── openai.py                    # OpenAI
        ├── openai_compatible_provider.py# Shared base for OpenAI-compatible APIs
        ├── openrouter.py                # OpenRouter
        └── xai.py                       # xAI (Grok)
```

---

### **File-by-File Analysis**

#### **1. Core API Clients**

**1. `__init__.py` (6 lines)**
Empty package marker with a docstring. No re-exports from the top level.

**2. `http_client.py` (173 lines)**
* **Purpose:** Generic async HTTP client for streaming file downloads. No authentication built-in (callers supply pre-authenticated URLs like Azure SAS).
* **Key Class:** `HttpClient` — wraps `httpx.AsyncClient` with generous timeouts (60s general, 300s read) and connection pooling (100 max, 40 keepalive).
* **Methods:**
    * `download_url_to_bytes()`: Streams URL into memory `bytearray` (supports heartbeat callback).
    * `download_url_to_file()`: Streams to disk using an atomic `.part` file pattern.
    * `close()`: Releases the connection pool.
* **Pattern:** Singleton via `get_http_client()` module-level function.

**3. `express_client.py` (365 lines)**
* **Purpose:** Async client for the Express backend's AI service endpoints. Used for tenant-scoped resource/file/enriched-image CRUD operations. Authenticated via `X-API-Key` header.
* **Core Infrastructure:** `_request()` (central HTTP method with `tenacity` retry on 5xx/connection errors), `_is_retryable()`, and `handle_api_errors()` (decorator for logging).
* **Endpoints** (Tenant-scoped under `/api/ai-service/tenants/{tenant_id}/`):

| Method | Endpoint | Purpose |
| :--- | :--- | :--- |
| `get_resources()` | `GET /resources` | Fetch resources with optional filters |
| `get_resource_by_id()` | `GET /resources/{id}` | Single resource fetch |
| `update_resource()` | `PUT /resources/{id}` | Update resource fields |
| `get_file_by_id()` | `GET /files/{id}` | Fetch file metadata |
| `create_file()` | `POST /files` | Create file record |
| `create_enriched_image()` | `POST /enriched-images` | Create enriched image |
| `update_enriched_image()` | `PUT /enriched-images/{id}`| Update enriched image |
| `get_enriched_images()` | `GET /enriched-images` | List enriched images |
| `get_tenant_storage()` | `GET /tenant-storage` | Fetch Azure storage config |

**4. `strapi_client.py` (900 lines)**
* **Purpose:** Async client for Strapi v4 CMS API. Handles machine types, resources, bundles, AI providers, and workflows. Authenticated via Bearer token (`STRAPI_API_URL`, `STRAPI_API_TOKEN`).
* **Core Infrastructure:** `_fetch()`/`_put()`, `handle_api_errors()` decorator (duplicated from ExpressClient), and Singleton via `get_strapi_client()`.
* **Method Groups:**
    * **Resource CRUD:** `get_resource_fields()`, `update_resource_fields()`, `get_resource_doc_download_info()`.
    * **Machine Type Operations:** `fetch_all_machine_types()`, `fetch_ai_providers_for_machine_type()`, `fetch_resource_bundles()`, `fetch_resources_for_machine_type()`.
    * **Resource Bundle Operations:** `create_resource_bundle()`, `update_resource_bundle_resources()`, `fetch_resources_for_bundle()`.
    * **AI Provider Metadata:** Upsert patterns for file uploads (`upsert_ai_provider_resource_upload_entry()`) and bundle indexes.
    * **Ingestion Workflow:** `fetch_ingestion_data()`, `fetch_resources_ingestion_data()` (3-priority fetch strategy with pagination).
    * **File Download:** `download_url_to_file()`, `download_url_to_bytes()` (duplicated from `HttpClient`).

---

#### **2. LLM Provider Abstraction (`ai_provider_client/`)**

**5. `__init__.py` (9 lines)**
Public API exports: `LLMClient`, `DotDict`, `LLMProviderError`, `AIParams`, `KeyPoolClient`, `PoolKey`.

**6. `ai_params.py` (73 lines)**
* **Purpose:** Normalizes API parameters across providers to handle quirks via `AIParams.build()`.
* **Quirks Handled:** Maps `max_tokens` to `max_completion_tokens` for OpenAI reasoning models. Injects `max_tokens: 4096` and system-level JSON instructions for Anthropic.

**7. `base_provider.py` (41 lines)**
Abstract base class defining the contract: `call()`, `call_sync()`, `embed()`, `embed_sync()`, `close()`, `close_sync()`.

**8. `client.py` (296 lines)**
* **Purpose:** The main orchestrator (`LLMClient`).
* **Features:**
    * **Lazy Init:** Providers created on first use via `PROVIDER_REGISTRY`.
    * **Retry:** Uses `tenacity` on HTTP 429, 500, 502, 503, 504.
    * **Key Pool Integration:** Uses `KeyPoolClient` to fetch keys from Express backend. On 429, exhausts the key, evicts provider, and retries. Fire-and-forget LRU tracking on success.
    * **Dual API:** Supports both async and sync variants.

**9. `dot_dict.py` (27 lines)**
`dict` subclass enabling attribute-style access (e.g., `response.choices[0].message.content`).

**10. `exceptions.py` (36 lines)**
Unified `LLMProviderError` formatted as `[provider] HTTP {code}: message`.

**11. `key_pool_client.py` (166 lines)**
HTTP client for the Express backend's centralized API key pool. Uses `PoolKey` dataclass. Handles `get_pool_sync()`, `touch_key()`, and `exhaust_key()` without blocking LLM calls.

---

#### **3. Individual AI Providers (`providers/`)**

**12. `__init__.py` (47 lines)**
Lazy-loading registry mapping string names to loader functions and environment variables (`openrouter`, `openai`, `azure_openai`, `google`, `anthropic`, `xai`).

**13. `openai_compatible_provider.py` (74 lines)**
Shared base for OpenAI-SDK-compatible providers. Supports pool-override kwargs.

**14-16. Open-AI Based Subclasses**
* **`openai.py` (15 lines):** Sets `env_var="OPENAI_API_KEY"`.
* **`xai.py` (19 lines):** Sets `env_var="XAI_API_KEY"` and `XAI_BASE_URL`.
* **`azure_openai.py` (63 lines):** Requires Endpoint, Key, and API version. Full call/embed/close support.

**17. `anthropic.py` (98 lines)**
Translates OpenAI-format messages to Anthropic's format (separates system messages, maps parameters). Normalizes output back to OpenAI format. No embedding support.

**18. `google.py` (102 lines)**
Translates messages for `google.genai.Client` (maps roles, `max_output_tokens`, `response_mime_type`). No embedding support.

**19. `openrouter.py` (68 lines)**
Direct HTTP calls via `httpx` with 300s timeout (bypasses OpenAI SDK). No embedding support.

**20. `README.md` (207 lines)**
Comprehensive documentation on architecture, fallbacks, and quick start logic.

---

### **Architectural Patterns**

| Pattern | Where Used | Description |
| :--- | :--- | :--- |
| **Singleton** | `HttpClient`, `StrapiClient` | Module-level `get_*()` functions ensure single instances. |
| **Strategy / Registry**| `PROVIDER_REGISTRY`, `BaseProvider` | Interchangeable LLM providers behind a unified API. |
| **Lazy Loading** | `providers/__init__.py` | Provider imports deferred until first execution. |
| **Decorator** | `express_client.py`, `strapi_client.py`| `handle_api_errors()` for consistent error logging. |
| **Adapter** | `anthropic.py`, `google.py` | Normalizes proprietary inputs/outputs to the OpenAI standard. |
| **Pool + Fallback** | `KeyPoolClient` | Dynamic key pooling falling back to static environment variables. |
| **Atomic Write** | `HttpClient`, `StrapiClient` | `.part` file pattern prevents corrupted file downloads. |
| **Retry w/ Backoff**| `ExpressClient`, `LLMClient` | `tenacity` handles transient network/API failures. |

---

### **Dependencies**

* **`httpx`**: Powers all HTTP clients (async + sync).
* **`tenacity`**: Drives retry and backoff logic.
* **`openai` SDK**: Used for OpenAI, Azure OpenAI, and XAI providers.
* **`anthropic` SDK**: Used for the Anthropic provider.
* **`google-genai`**: Used for the Google Gemini provider.

---

### **Notable Observations**

* **Code Duplication:** `StrapiClient` contains `download_url_to_file()` and `download_url_to_bytes()` which are nearly identical to methods in `HttpClient`. The `handle_api_errors` decorator is also duplicated across clients.
* **N+1 Query Pattern:** `fetch_resources_ingestion_data()` in the Strapi client calls `_fetch_org_for_machine_type()` for every resource. This can result in hundreds of sequential HTTP calls.
* **Deprecated API Usage:** `datetime.utcnow()` is used in `strapi_client.py` (deprecated since Python 3.12).
* **Universal Interface:** All LLM responses are normalized to the OpenAI format (`{"choices": [{"message": {"content": ...}}], "usage": {...}}`), allowing consumers to treat all 6 providers completely identically.

---