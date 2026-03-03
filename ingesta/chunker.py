"""
chunker.py — Conversión de filas de tabla a texto natural para embeddings.

La función fila_a_texto() usa las categorías semánticas del mapeo definido por
el usuario: nombre, identificador, datos. Los campos vacíos se omiten para
producir texto limpio independientemente del tipo de documento.
"""

from loguru import logger


def fila_a_texto(fila: dict) -> str:
    """
    Convierte una fila procesada (con claves semánticas) a texto natural en español.

    La fila debe tener las claves: nombre, identificador, datos.
    (producidas por extractor._aplicar_mapeo)

    Los campos vacíos se omiten. El campo 'datos' ya contiene los nombres de
    columna originales como etiquetas: "Col1: val1 | Col2: val2".

    Ejemplo de salida (documento UE):
        Sustancia: Limoneno.
        Identificador: CAS 138-86-3 | FL 491.
        Datos: Restricciones de uso: Solo cat. IV | Pureza: ≥ 95%.

    Ejemplo de salida (documento Japón):
        Sustancia: 酢酸.
        Datos: 規制値: 0.1mg | 用途: 食品添加物.
    """
    lineas = []

    nombre = fila.get("nombre", "").strip() or "Sin denominación registrada."
    lineas.append(f"Sustancia: {nombre}.")

    identificador = fila.get("identificador", "").strip()
    if identificador:
        lineas.append(f"Identificador: {identificador}.")

    datos = fila.get("datos", "").strip()
    if datos:
        lineas.append(f"Datos: {datos}.")

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
        numero = chunk.get('numero_articulo') or f"p{chunk.get('pagina', i)}"
        sub = chunk.get('sub_chunk', '1/1').replace('/', '_')
        doc_id = f"{nombre_archivo}_articulo_{numero}_{sub}_{i}"
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
