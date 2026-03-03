# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
# Install dependencies (--prefer-binary avoids C++ compilation of chroma-hnswlib)
pip install --prefer-binary -r requirements.txt

# Start the Streamlit app (app.py is at the project root, not in a subdirectory)
streamlit run app.py

# Or using the Windows script
iniciar.bat
```

The app runs at `http://localhost:8501` by default. There is no test suite.

> **Python 3.13 note**: `chroma-hnswlib` requires compilation if no pre-built wheel exists for the running Python version. `iniciar.bat` uses `--prefer-binary` to prefer wheels. If it still fails, install [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) with the "Desktop development with C++" workload, or use Python 3.11/3.12.

## Environment Setup

Copy `.env.example` to `.env` (both at the project root) and fill in:

```env
OPENAI_API_KEY=sk-...          # Required

# MySQL (optional — enables analytical queries)
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=
MYSQL_PASSWORD=
MYSQL_DATABASE=rag_regulatorio
```

MySQL is optional. Without it, the app degrades gracefully: analytical queries fall back to ChromaDB semantic search.

## Architecture

### Hybrid RAG System

The system routes queries through two paths based on intent:

**Semantic path** (default): PDF text → OpenAI embeddings → ChromaDB → re-ranked retrieval → GPT-4o
**Analytical path** (if MySQL available): PDF tables → MySQL → SQL template or GPT-generated SELECT → GPT-4o formatting

Query routing is handled by `motor/router.py`: specific FL/CAS/article numbers always go semantic; count/list/enumerate patterns go analytical.

### Module Responsibilities

| Module | Role |
|--------|------|
| `ingesta/extractor.py` | PDF structure detection, article chunking, table extraction |
| `ingesta/chunker.py` | Converts rows/chunks to `{id, texto, metadata}` dicts for indexing |
| `ingesta/ingest.py` | Orchestrates ChromaDB + MySQL writes; manages embeddings in batches of 50 |
| `motor/router.py` | Classifies query as `'semantico'` or `'analitico'` |
| `motor/retriever.py` | Vector search + re-ranking (top_k=12 → 3–6 final chunks) |
| `motor/generator.py` | GPT-4o response generation for both semantic and analytical results |
| `motor/sql_manager.py` | MySQL connection, database + schema auto-creation, CRUD, parameterized queries |
| `motor/sql_executor.py` | 8 regex templates → GPT fallback for SQL generation; validates SELECT-only |
| `app.py` | Streamlit UI: Chat tab + Administration tab |

### Key Design Decisions

**Dynamic column mapping**: Users define semantic categories (nombre/identificador/datos/ignorar) per document. No column names are hardcoded. `datos` columns preserve their original name as a label ("ColName: value | ColName2: value2"), making the system generic across different document types. Mappings persist in ChromaDB chunk metadata.

**Chunking**: Text pages and articles split at ~200 words with 40-word overlap (applied to both article and non-article pages). Table rows converted to natural Spanish via `chunker.fila_a_texto()`.

**Retrieval scoring**: ChromaDB cosine distance converted to similarity (`1 - distance/2`). Scores boosted by +0.15 for legal-term matches (bilingual: Spanish + English), +0.20 for FL/CAS identifier hits. Score < 0.65 shows a "low relevance" badge in the UI. `top_k=12` candidates retrieved before re-ranking to 3–6 final chunks.

**SQL safety**: `sql_executor._validar_sql()` whitelists SELECT only — no DDL or DML allowed through the GPT-generated path.

**ChromaDB collection**: Named `"reglamento_regulatorio"`, stored in `rag_regulatorio/db_chroma/`.

**MySQL schema**:
```sql
documentos(id, nombre, tipo, total_sustancias, fecha_ingesta)
sustancias(id, documento_id, nombre, identificador, numero_fl, numero_cas,
           datos, pagina, raw_json)
```

`datos` replaces the former `restriccion/pureza/nota` columns. It stores all non-name/non-identifier columns as a single text field with format `"ColName: value | ColName2: value2"`, making the schema generic across document types. `numero_fl` and `numero_cas` are extracted from `identificador` via regex for indexed lookup. `raw_json` preserves the full original row. All rows share a single `sustancias` table linked via `documento_id` FK.

### Data Flow (PDF Ingestion)

```
Upload PDF → analizar_estructura_pdf() → User maps columns (UI)
  → extraer_texto_narrativo() + extraer_tabla_sustancias()
  → chunks_*_a_documentos()
  → indexar_documentos() → ChromaDB + MySQL (if available)
```

### Data Flow (Query)

```
User query → clasificar_query()
  → 'analitico' + MySQL: ejecutar_analitico() → formatear_resultado_sql()
  → 'semantico' or no MySQL: buscar() → generar_respuesta()
```

## Dependencies

- `pdfplumber==0.11.0` — PDF parsing and table detection
- `chromadb==0.5.23` — local vector database
- `openai>=2.0.0` — embeddings (`text-embedding-3-small`) and generation (`gpt-4o`)
- `streamlit==1.39.0` — web UI
- `pymysql>=1.1.0` — MySQL driver
- `pandas==2.2.3` — result formatting
- `loguru==0.7.2` — structured logging
