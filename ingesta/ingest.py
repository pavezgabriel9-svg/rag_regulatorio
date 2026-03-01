"""
ingest.py — Carga de documentos en ChromaDB y gestión de la colección.

Colección única: reglamento_regulatorio
Embeddings: text-embedding-3-small (OpenAI) en batches de 50

Las filas de tabla también se escriben a MySQL (sql_manager).
Si MySQL no está disponible, la ingesta continúa solo en ChromaDB.
"""

import os
from datetime import datetime
from pathlib import Path

import chromadb
from chromadb import Settings as ChromaSettings
from openai import OpenAI
from dotenv import load_dotenv
from loguru import logger

from motor import sql_manager

load_dotenv()

COLECCION = "reglamento_regulatorio"
DB_PATH = Path(__file__).resolve().parent.parent / "db_chroma"
BATCH_SIZE = 50


_CHROMA_SETTINGS = ChromaSettings(anonymized_telemetry=False)


def _get_cliente_chroma() -> chromadb.PersistentClient:
    DB_PATH.mkdir(exist_ok=True)
    return chromadb.PersistentClient(path=str(DB_PATH), settings=_CHROMA_SETTINGS)


def _get_coleccion():
    cliente = _get_cliente_chroma()
    return cliente.get_or_create_collection(
        name=COLECCION,
        metadata={"hnsw:space": "cosine"},
    )


def _get_openai() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY no está configurada en .env")
    return OpenAI(api_key=api_key, timeout=60.0)


def _generar_embeddings(textos: list[str], cliente: OpenAI) -> list[list[float]]:
    """Genera embeddings en batches de BATCH_SIZE."""
    embeddings = []
    for i in range(0, len(textos), BATCH_SIZE):
        batch = textos[i: i + BATCH_SIZE]
        respuesta = cliente.embeddings.create(
            model="text-embedding-3-small",
            input=batch,
        )
        embeddings.extend([e.embedding for e in respuesta.data])
        logger.debug(f"Embeddings batch {i // BATCH_SIZE + 1}: {len(batch)} textos")
    return embeddings


def indexar_documentos(
    documentos: list[dict],
    nombre_archivo: str,
    mapeo_columnas: dict = None,
    filas_tabla: list[dict] = None,
    tipo_documento: str = "mixto",
) -> int:
    """
    Indexa una lista de documentos en ChromaDB y, si hay filas de tabla,
    también las escribe en MySQL.

    Cada documento debe tener: {id, texto, metadata}
    Evita duplicados verificando IDs existentes.

    Args:
        documentos:      lista producida por chunker.chunks_*_a_documentos()
        nombre_archivo:  nombre del PDF (para metadata de fecha_ingesta)
        mapeo_columnas:  mapeo usado (se persiste en metadata del primer chunk)
        filas_tabla:     filas semánticas del extractor para escribir a MySQL
        tipo_documento:  tipo detectado ('mixto'|'solo_tabla'|'solo_texto')

    Retorna número de chunks efectivamente insertados en ChromaDB.
    """
    if not documentos:
        logger.warning("No hay documentos para indexar")
        return 0

    coleccion = _get_coleccion()
    openai_cliente = _get_openai()

    # Verificar IDs ya existentes para evitar duplicados
    ids_nuevos = [doc["id"] for doc in documentos]
    try:
        existentes = coleccion.get(ids=ids_nuevos)
        ids_existentes = set(existentes["ids"])
    except Exception:
        ids_existentes = set()

    docs_a_insertar = [d for d in documentos if d["id"] not in ids_existentes]
    if not docs_a_insertar:
        logger.info(f"Todos los chunks de {nombre_archivo} ya estaban indexados")
        return 0

    fecha_ingesta = datetime.now().isoformat()

    # Preparar datos
    ids = []
    textos = []
    metadatas = []

    for i, doc in enumerate(docs_a_insertar):
        meta = {**doc["metadata"], "fecha_ingesta": fecha_ingesta}
        # Persistir mapeo en el primer chunk del documento
        if i == 0 and mapeo_columnas:
            import json
            meta["mapeo_columnas"] = json.dumps(mapeo_columnas)
        # ChromaDB solo acepta str/int/float/bool en metadata
        meta = {k: str(v) if not isinstance(v, (str, int, float, bool)) else v
                for k, v in meta.items()}
        ids.append(doc["id"])
        textos.append(doc["texto"])
        metadatas.append(meta)

    logger.info(f"Generando embeddings para {len(textos)} chunks...")
    embeddings = _generar_embeddings(textos, openai_cliente)

    coleccion.add(
        ids=ids,
        documents=textos,
        embeddings=embeddings,
        metadatas=metadatas,
    )

    logger.info(f"Indexados: {len(ids)} chunks de {nombre_archivo}")

    # --- Escritura paralela a MySQL (solo si hay filas de tabla) ---
    if filas_tabla:
        try:
            sql_manager.init_db()
            doc_id = sql_manager.insertar_documento(nombre_archivo, tipo_documento)
            if doc_id is not None:
                n_sql = sql_manager.insertar_sustancias(doc_id, filas_tabla)
                logger.info(f"MySQL — {n_sql} sustancias escritas para {nombre_archivo}")
            else:
                logger.warning("MySQL — no se pudo obtener documento_id, sustancias no escritas")
        except Exception as e:
            logger.error(f"MySQL — error en escritura paralela (ChromaDB no afectado): {e}")

    return len(ids)


def listar_documentos() -> list[dict]:
    """
    Retorna la lista de documentos únicos indexados con sus estadísticas.

    Retorna lista de dicts:
        {nombre, total_chunks, articulos, sustancias, fecha_ingesta}
    """
    try:
        coleccion = _get_coleccion()
        todos = coleccion.get(include=["metadatas"])
        metadatas = todos["metadatas"] or []
    except Exception as e:
        logger.error(f"Error consultando ChromaDB: {e}")
        return []

    docs: dict[str, dict] = {}
    for meta in metadatas:
        nombre = meta.get("documento", "desconocido")
        if nombre not in docs:
            docs[nombre] = {
                "nombre": nombre,
                "total_chunks": 0,
                "articulos": 0,
                "sustancias": 0,
                "fecha_ingesta": meta.get("fecha_ingesta", ""),
            }
        docs[nombre]["total_chunks"] += 1
        tipo = meta.get("tipo", "")
        if tipo == "articulo" or tipo == "texto":
            docs[nombre]["articulos"] += 1
        elif tipo == "sustancia":
            docs[nombre]["sustancias"] += 1

    return list(docs.values())


def eliminar_documento(nombre_archivo: str) -> int:
    """
    Elimina todos los chunks de un documento de ChromaDB y MySQL.

    Retorna número de chunks eliminados de ChromaDB.
    """
    try:
        coleccion = _get_coleccion()
        resultado = coleccion.get(
            where={"documento": nombre_archivo},
            include=["metadatas"],
        )
        ids = resultado["ids"]
        if not ids:
            logger.warning(f"No se encontraron chunks para: {nombre_archivo}")
            return 0
        coleccion.delete(ids=ids)
        logger.info(f"Eliminados {len(ids)} chunks de ChromaDB: {nombre_archivo}")
    except Exception as e:
        logger.error(f"Error eliminando de ChromaDB {nombre_archivo}: {e}")
        return 0

    # Eliminar también de MySQL (CASCADE borra sustancias automáticamente)
    try:
        n_sql = sql_manager.eliminar_documento(nombre_archivo)
        if n_sql:
            logger.info(f"MySQL — {n_sql} sustancias eliminadas: {nombre_archivo}")
    except Exception as e:
        logger.error(f"MySQL — error al eliminar {nombre_archivo}: {e}")

    return len(ids)


def obtener_mapeo_guardado(nombre_archivo: str) -> dict | None:
    """
    Recupera el mapeo de columnas guardado para un documento.
    Útil para re-indexar sin pedir el mapeo de nuevo.
    """
    import json
    try:
        coleccion = _get_coleccion()
        resultado = coleccion.get(
            where={"documento": nombre_archivo},
            include=["metadatas"],
            limit=1,
        )
        if resultado["metadatas"]:
            mapeo_str = resultado["metadatas"][0].get("mapeo_columnas")
            if mapeo_str:
                return json.loads(mapeo_str)
    except Exception as e:
        logger.warning(f"No se pudo recuperar mapeo para {nombre_archivo}: {e}")
    return None


def estado_sistema() -> dict:
    """Retorna métricas básicas del sistema para el sidebar."""
    try:
        coleccion = _get_coleccion()
        todos = coleccion.get(include=["metadatas"])
        metadatas = todos["metadatas"] or []
        documentos = set(m.get("documento", "") for m in metadatas)
        return {
            "conectado": True,
            "total_documentos": len(documentos),
            "total_chunks": len(metadatas),
        }
    except Exception as e:
        logger.error(f"Error estado sistema: {e}")
        return {"conectado": False, "total_documentos": 0, "total_chunks": 0}
