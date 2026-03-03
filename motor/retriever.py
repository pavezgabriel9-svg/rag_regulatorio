"""
retriever.py — Búsqueda vectorial en ChromaDB con re-ranking.

Estrategia:
  1. Recuperar top_k=8 chunks por similitud vectorial
  2. Re-ranking por tipo y presencia de identificadores
  3. Filtrar por score: garantizar mínimo 3, máximo 6 chunks al modelo
"""

import os
import re

import chromadb
from chromadb import Settings as ChromaSettings
from openai import OpenAI
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

COLECCION = "reglamento_regulatorio"
DB_PATH = str(__import__('pathlib').Path(__file__).resolve().parent.parent / "db_chroma")

TOP_K_RECUPERAR = 12
MIN_CHUNKS_MODELO = 3
MAX_CHUNKS_MODELO = 6
SCORE_ALTO = 0.75
SCORE_MEDIO = 0.55
SCORE_BAJO_LABEL = 0.65   # umbral para badge "relevancia baja" en UI

PALABRAS_LEGALES = [
    #Spanish regulatory terms
    "especificación", "estándar", "reglamento", "requisito",
    "permitido", "prohibido", "restringido", "anexo", "sección",
    "artículo", "párrafo", "provisión", "cláusula", "límite",
    "pureza", "identificación", "descripción", "prueba", "ensayo",
    # English regulatory terms
    "specification", "standard", "regulation", "requirement",
    "permitted", "prohibited", "restricted", "annex", "section",
    "article", "paragraph", "provision", "clause", "limit",
    "purity", "identification", "description", "test", "assay",
]


_CHROMA_SETTINGS = ChromaSettings(anonymized_telemetry=False)


def _get_coleccion():
    cliente = chromadb.PersistentClient(path=DB_PATH, settings=_CHROMA_SETTINGS)
    return cliente.get_or_create_collection(
        name=COLECCION,
        metadata={"hnsw:space": "cosine"},
    )


def _get_openai() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=60.0)


def _embedding_query(query: str, cliente: OpenAI) -> list[float]:
    respuesta = cliente.embeddings.create(
        model="text-embedding-3-small",
        input=[query],
    )
    return respuesta.data[0].embedding


def _contiene_palabras_legales(query: str) -> bool:
    query_lower = query.lower()
    return any(p in query_lower for p in PALABRAS_LEGALES)


def _extraer_identificadores(query: str) -> list[str]:
    """Extrae números FL, CAS u otros identificadores de la query."""
    patrones = [
        r'FL[\s\-]?(\d+)',
        r'CAS[\s\-]?(\d+[\-\d]*)',
        r'\b(\d{2,6}\-\d{2}\-\d{1})\b',
    ]
    encontrados = []
    for p in patrones:
        encontrados.extend(re.findall(p, query, re.IGNORECASE))
    return encontrados


def buscar(
    query: str,
    top_k: int = TOP_K_RECUPERAR,
    documento: str = None,
) -> list[dict]:
    """
    Busca chunks relevantes para la query.

    Args:
        query:     pregunta del usuario
        top_k:     cuántos chunks recuperar de ChromaDB antes del re-ranking
        documento: filtrar por documento específico (None = todos)

    Retorna lista de dicts (entre MIN y MAX chunks):
        {texto, score, documento, tipo, pagina, numero_fl, relevancia_baja}
    """
    coleccion = _get_coleccion()
    openai_cliente = _get_openai()

    embedding = _embedding_query(query, openai_cliente)

    # Filtro por documento si se especifica
    where = None
    if documento and documento != "Todos los documentos":
        where = {"documento": documento}

    try:
        resultados = coleccion.query(
            query_embeddings=[embedding],
            n_results=min(top_k, coleccion.count()),
            include=["documents", "metadatas", "distances"],
            where=where,
        )
    except Exception as e:
        logger.error(f"Error en búsqueda ChromaDB: {e}")
        return []

    documentos_raw = resultados["documents"][0] if resultados["documents"] else []
    metadatas_raw = resultados["metadatas"][0] if resultados["metadatas"] else []
    distancias = resultados["distances"][0] if resultados["distances"] else []

    if not documentos_raw:
        logger.warning("Sin resultados en ChromaDB")
        return []

    # Convertir distancia coseno a score de similitud [0, 1]
    # ChromaDB con cosine retorna distancia (0=idéntico, 2=opuesto)
    chunks = []
    for texto, meta, dist in zip(documentos_raw, metadatas_raw, distancias):
        score = max(0.0, 1.0 - dist / 2.0)
        chunks.append({
            "texto": texto,
            "score": round(score, 4),
            "documento": meta.get("documento", ""),
            "tipo": meta.get("tipo", ""),
            "pagina": int(meta.get("pagina", 0)),
            "numero_fl": meta.get("numero_fl", ""),
            "numero_articulo": meta.get("numero_articulo", ""),
            "identificador_completo": meta.get("identificador_completo", ""),
            "nombre_sustancia": meta.get("nombre_sustancia", ""),
        })

    # --- Re-ranking ---
    boost_legal = _contiene_palabras_legales(query)
    identificadores_query = _extraer_identificadores(query)

    for chunk in chunks:
        score_ajustado = chunk["score"]

        # Boost a artículos si la query tiene términos legales
        if boost_legal and chunk["tipo"] in ("articulo", "texto"):
            score_ajustado += 0.15

        # Boost si el chunk contiene un identificador mencionado en la query
        if identificadores_query:
            meta_ids = [
                chunk.get("numero_fl", ""),
                chunk.get("identificador_completo", ""),
            ]
            for id_query in identificadores_query:
                if any(id_query in str(m) for m in meta_ids if m):
                    score_ajustado += 0.20
                    break

        chunk["score_final"] = round(min(score_ajustado, 1.0), 4)

    chunks.sort(key=lambda x: x["score_final"], reverse=True)

    # --- Selección de chunks para el modelo ---
    seleccionados = []

    # Primero: score alto
    for c in chunks:
        if c["score_final"] >= SCORE_ALTO and len(seleccionados) < MAX_CHUNKS_MODELO:
            seleccionados.append(c)

    # Relleno hasta mínimo con score medio
    if len(seleccionados) < MIN_CHUNKS_MODELO:
        for c in chunks:
            if c not in seleccionados and c["score_final"] >= SCORE_MEDIO:
                seleccionados.append(c)
            if len(seleccionados) >= MIN_CHUNKS_MODELO:
                break

    # Garantía absoluta: si aún no hay 3, tomar los mejores disponibles
    if len(seleccionados) < MIN_CHUNKS_MODELO:
        for c in chunks:
            if c not in seleccionados:
                seleccionados.append(c)
            if len(seleccionados) >= MIN_CHUNKS_MODELO:
                break

    # Limitar a máximo
    seleccionados = seleccionados[:MAX_CHUNKS_MODELO]

    # Marcar chunks de baja relevancia para la UI
    for c in seleccionados:
        c["relevancia_baja"] = c["score_final"] < SCORE_BAJO_LABEL

    logger.info(
        f"Retrieval: {len(seleccionados)} chunks seleccionados "
        f"(scores: {[c['score_final'] for c in seleccionados]})"
    )
    return seleccionados
