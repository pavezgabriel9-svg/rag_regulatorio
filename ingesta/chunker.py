"""
chunker.py — Conversión de filas de tabla a texto natural para embeddings.

La función fila_a_texto() es dinámica: usa las categorías semánticas del mapeo
definido por el usuario, no nombres de columnas hardcodeados.
"""

from loguru import logger


# Textos de reemplazo cuando un campo está vacío
_VACIOS = {
    "nombre":       "Sin denominación registrada.",
    "identificador": "Sin identificador registrado.",
    "restriccion":  "Sin restricciones específicas de uso.",
    "pureza":       "No especificada.",
    "nota":         "Sin nota regulatoria adicional.",
}

# Etiquetas en español para cada categoría semántica
_ETIQUETAS = {
    "nombre":        "Sustancia",
    "identificador": "Identificador",
    "restriccion":   "Restricciones de uso",
    "pureza":        "Pureza mínima requerida",
    "nota":          "Nota regulatoria",
}


def fila_a_texto(fila: dict) -> str:
    """
    Convierte una fila procesada (con claves semánticas) a texto natural en español.

    La fila debe tener las claves: nombre, identificador, restriccion, pureza, nota.
    (producidas por extractor._aplicar_mapeo)

    Ejemplo de salida:
        Sustancia: Limoneno.
        Identificador: CAS 138-86-3 | FL 491.
        Restricciones de uso: Sin restricciones específicas de uso.
        Pureza mínima requerida: 95%.
        Nota regulatoria: Sin nota regulatoria adicional.
    """
    lineas = []

    for categoria in ["nombre", "identificador", "restriccion", "pureza", "nota"]:
        valor = fila.get(categoria, "").strip()
        if not valor:
            valor = _VACIOS[categoria]
        etiqueta = _ETIQUETAS[categoria]
        lineas.append(f"{etiqueta}: {valor}.")

    return "\n".join(lineas)


def chunks_texto_a_documentos(
    chunks: list[dict],
    nombre_archivo: str,
) -> list[dict]:
    """
    Prepara chunks de texto narrativo para indexar en ChromaDB.

    Agrega el campo 'documento' y normaliza la estructura.

    Retorna lista de dicts:
        {id, texto, metadata: {documento, tipo, pagina, numero_articulo, sub_chunk}}
    """
    documentos = []
    for i, chunk in enumerate(chunks):
        doc_id = f"{nombre_archivo}_articulo_{chunk.get('numero_articulo', i)}_{chunk.get('sub_chunk', '1/1').replace('/', '_')}"
        documentos.append({
            "id": doc_id,
            "texto": chunk["texto"],
            "metadata": {
                "documento": nombre_archivo,
                "tipo": chunk.get("tipo", "articulo"),
                "pagina": chunk.get("pagina", 0),
                "numero_articulo": str(chunk.get("numero_articulo", "")),
                "sub_chunk": chunk.get("sub_chunk", "1/1"),
                "numero_fl": "",
            },
        })

    logger.info(f"Texto narrativo preparado: {len(documentos)} documentos")
    return documentos


def chunks_tabla_a_documentos(
    filas: list[dict],
    nombre_archivo: str,
) -> list[dict]:
    """
    Convierte filas de tabla a documentos listos para ChromaDB.

    Cada fila se convierte a texto natural con fila_a_texto() y se almacena
    junto con su metadata raw.

    Retorna lista de dicts:
        {id, texto, metadata: {documento, tipo, pagina, numero_fl, ...raw}}
    """
    documentos = []
    for i, fila in enumerate(filas):
        texto = fila_a_texto(fila)
        raw = fila.get("_raw", {})

        # Intentar extraer un identificador corto para el ID del chunk
        identificador = fila.get("identificador", "").strip()
        id_corto = identificador[:20].replace(" ", "_").replace("/", "-") if identificador else str(i)
        doc_id = f"{nombre_archivo}_sustancia_{id_corto}_{i}"

        # Guardar el identificador en metadata para re-ranking posterior
        documentos.append({
            "id": doc_id,
            "texto": texto,
            "metadata": {
                "documento": nombre_archivo,
                "tipo": "sustancia",
                "pagina": fila.get("pagina", 0),
                "numero_fl": _extraer_numero_fl(identificador),
                "identificador_completo": identificador,
                "nombre_sustancia": fila.get("nombre", ""),
            },
        })

    logger.info(f"Tabla preparada: {len(documentos)} documentos")
    return documentos


def _extraer_numero_fl(identificador: str) -> str:
    """Intenta extraer el número FL del campo identificador si existe."""
    import re
    match = re.search(r'FL[\s\-]?(\d+)', identificador, re.IGNORECASE)
    if match:
        return match.group(1)
    return ""
