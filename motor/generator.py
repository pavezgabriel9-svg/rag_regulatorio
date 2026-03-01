"""
generator.py — Construcción del prompt y llamada a GPT-4o.

Dos modos:
  - semantico:  respuesta detallada con citas [N]
  - analitico:  respuesta estructurada para conteos y listados
"""

import os
from openai import OpenAI
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

MODELO = "gpt-4o"
MAX_TOKENS = 1500
TEMPERATURE = 0
PREVIEW_CHARS = 150  # caracteres del chunk a mostrar en UI

SYSTEM_PROMPT = """Eres un experto regulatorio especializado en normativas de sustancias aromatizantes \
y reglamentos de la Unión Europea. Tu conocimiento proviene exclusivamente de los \
documentos que el área de regulaciones ha indexado en el sistema.

Reglas estrictas:
1. Responde ÚNICAMENTE con información del contexto proporcionado.
2. Si la información no está en el contexto, di: "Esta consulta no tiene respuesta \
en los documentos disponibles. Te recomiendo revisar el documento original."
3. Siempre menciona el número FL y/o CAS al referirte a una sustancia específica.
4. Si hay restricciones de uso, destácalas claramente.
5. Cita la fuente usando el número entre corchetes: [1], [2], etc.
6. Responde en español, con precisión técnica pero lenguaje claro."""

SYSTEM_PROMPT_ANALITICO = """Eres un experto regulatorio especializado en normativas de sustancias aromatizantes \
y reglamentos de la Unión Europea.

Con base en los fragmentos del reglamento proporcionados, responde la siguiente \
consulta analítica. Si los fragmentos no contienen suficiente información para \
dar un número exacto, indícalo y da una estimación basada en lo disponible. \
Sé preciso y estructura la respuesta en formato de lista cuando sea apropiado. \
Cita la fuente usando el número entre corchetes: [1], [2], etc. \
Responde en español."""


def _get_openai() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=60.0)


def _construir_contexto(chunks: list[dict]) -> tuple[str, list[dict]]:
    """
    Construye el bloque de contexto numerado y la lista de fuentes para la UI.

    Retorna:
        contexto_texto: string con [1] texto... [2] texto...
        fuentes:        lista de dicts para mostrar en el expander de la UI
    """
    lineas_contexto = []
    fuentes = []

    for i, chunk in enumerate(chunks, start=1):
        # Encabezado de fuente
        tipo_label = "Artículo" if chunk["tipo"] in ("articulo", "texto") else "Sustancia"
        articulo_info = ""
        if chunk.get("numero_articulo"):
            articulo_info = f" · Art. {chunk['numero_articulo']}"
        if chunk.get("sub_chunk") and chunk["sub_chunk"] != "1/1":
            articulo_info += f" ({chunk['sub_chunk']})"

        encabezado = (
            f"[{i}] ({chunk['documento']} — {tipo_label}{articulo_info}, pág. {chunk['pagina']})"
        )
        lineas_contexto.append(encabezado)
        lineas_contexto.append(chunk["texto"])
        lineas_contexto.append("")

        # Fuente para la UI
        preview = chunk["texto"][:PREVIEW_CHARS]
        if len(chunk["texto"]) > PREVIEW_CHARS:
            preview += "..."

        fuentes.append({
            "numero": i,
            "documento": chunk["documento"],
            "tipo": tipo_label,
            "pagina": chunk["pagina"],
            "score": chunk.get("score_final", chunk.get("score", 0)),
            "relevancia_baja": chunk.get("relevancia_baja", False),
            "preview": preview,
            "numero_articulo": chunk.get("numero_articulo", ""),
            "sub_chunk": chunk.get("sub_chunk", ""),
        })

    return "\n".join(lineas_contexto).strip(), fuentes


def generar_respuesta(pregunta: str, chunks: list[dict]) -> dict:
    """
    Genera una respuesta semántica usando GPT-4o.

    Retorna:
        {respuesta, fuentes, tipo_query, chunks_usados}
    """
    cliente = _get_openai()
    contexto, fuentes = _construir_contexto(chunks)

    # Advertencia al modelo si los chunks son de baja relevancia
    todos_bajos = all(c.get("relevancia_baja", False) for c in chunks)
    nota_relevancia = (
        "\n\nNOTA: Los fragmentos disponibles tienen relevancia baja para esta consulta. "
        "Si no puedes responder con certeza, indícalo explícitamente."
        if todos_bajos else ""
    )

    user_message = (
        f"Contexto de los documentos regulatorios:\n\n"
        f"{contexto}"
        f"{nota_relevancia}\n\n"
        f"Pregunta: {pregunta}"
    )

    logger.info(f"Generando respuesta semántica con {len(chunks)} chunks")
    respuesta = cliente.chat.completions.create(
        model=MODELO,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )

    texto_respuesta = respuesta.choices[0].message.content.strip()
    logger.info(f"Respuesta generada: {len(texto_respuesta)} chars")

    return {
        "respuesta": texto_respuesta,
        "fuentes": fuentes,
        "tipo_query": "semantico",
        "chunks_usados": len(chunks),
    }


def formatear_resultado_sql(pregunta: str, filas: list[dict], sql_usado: str, modo: str) -> dict:
    """
    Formatea el resultado de una query MySQL en lenguaje natural usando GPT-4o.

    Args:
        pregunta:  pregunta original del usuario
        filas:     resultados de MySQL (lista de dicts)
        sql_usado: SQL ejecutado (para transparencia)
        modo:      'template' | 'gpt'

    Retorna:
        {respuesta, tipo_query, filas_sql, sql_usado, modo_sql}
    """
    cliente = _get_openai()

    if not filas:
        return {
            "respuesta": "La consulta no arrojó resultados en la base de datos.",
            "tipo_query": "analitico_sql",
            "filas_sql": [],
            "sql_usado": sql_usado,
            "modo_sql": modo,
        }

    # Serializar filas como texto tabular para el prompt
    columnas = list(filas[0].keys())
    encabezado = " | ".join(columnas)
    separador = "-" * len(encabezado)
    filas_texto = "\n".join(
        " | ".join(str(fila.get(col, "")) for col in columnas)
        for fila in filas[:100]  # limitar contexto
    )
    resumen_filas = f"({len(filas)} filas totales)" if len(filas) > 100 else ""

    contexto = f"{encabezado}\n{separador}\n{filas_texto}\n{resumen_filas}".strip()

    system = (
        "Eres un experto regulatorio. El usuario hizo una consulta analítica sobre sustancias "
        "o documentos regulatorios. Se te proporciona el resultado de una consulta SQL. "
        "Resume y presenta la información en lenguaje natural claro en español. "
        "Si son muchas filas, agrupa o resume. Si es un conteo, responde directamente. "
        "No menciones SQL ni detalles técnicos en tu respuesta."
    )
    user = f"Resultados de la consulta:\n\n{contexto}\n\nPregunta del usuario: {pregunta}"

    logger.info(f"Formateando resultado SQL con GPT ({len(filas)} filas)")
    respuesta = cliente.chat.completions.create(
        model=MODELO,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )

    texto = respuesta.choices[0].message.content.strip()
    return {
        "respuesta": texto,
        "tipo_query": "analitico_sql",
        "filas_sql": filas,
        "sql_usado": sql_usado,
        "modo_sql": modo,
    }


def analizar_consulta_analitica(pregunta: str, chunks: list[dict]) -> dict:
    """
    Genera una respuesta analítica (conteos, listados) usando GPT-4o.

    Retorna:
        {respuesta, fuentes, tipo_query, chunks_usados}
    """
    cliente = _get_openai()
    contexto, fuentes = _construir_contexto(chunks)

    user_message = (
        f"Fragmentos del reglamento:\n\n"
        f"{contexto}\n\n"
        f"Consulta analítica: {pregunta}"
    )

    logger.info(f"Generando respuesta analítica con {len(chunks)} chunks")
    respuesta = cliente.chat.completions.create(
        model=MODELO,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_ANALITICO},
            {"role": "user", "content": user_message},
        ],
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )

    texto_respuesta = respuesta.choices[0].message.content.strip()
    logger.info(f"Respuesta analítica generada: {len(texto_respuesta)} chars")

    return {
        "respuesta": texto_respuesta,
        "fuentes": fuentes,
        "tipo_query": "analitico",
        "chunks_usados": len(chunks),
    }
