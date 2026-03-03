"""
sql_executor.py — Motor híbrido de queries analíticas.

Estrategia:
  1. Intentar match con templates predefinidos (regex sobre la query)
  2. Si no hay match → GPT genera SQL usando el schema
  3. Validar que sea SELECT antes de ejecutar
  4. Ejecutar en MySQL via sql_manager
"""

import os
import re

from openai import OpenAI
from dotenv import load_dotenv
from loguru import logger

from motor.sql_manager import ejecutar_select, get_schema_info

load_dotenv()

MODELO = "gpt-4o"

# ---------------------------------------------------------------------------
# Templates predefinidos
# ---------------------------------------------------------------------------

# Cada template: (lista de patrones regex, función que genera (sql, params))
# La función recibe el match y el documento_filtro (puede ser None)

def _where_doc(documento: str | None) -> tuple[str, tuple]:
    """Genera cláusula WHERE y params para filtro por documento."""
    if documento and documento != "Todos los documentos":
        return (
            "JOIN documentos d ON s.documento_id = d.id WHERE d.nombre = %s",
            (documento,),
        )
    return ("", ())


TEMPLATES = [
    # 1. Cuántas sustancias
    {
        "patrones": [r'cu[aá]ntas?\s+sustancias?', r'total\s+de\s+sustancias?', r'cuantos?\s+registros?'],
        "builder": lambda m, doc: _tpl_contar_sustancias(doc),
    },
    # 2. Lista todas las sustancias
    {
        "patrones": [r'listar?\s+todas?', r'dame\s+todas?', r'mostrar\s+todas?', r'lista\s+de\s+sustancias?', r'todas?\s+las?\s+sustancias?'],
        "builder": lambda m, doc: _tpl_listar_todas(doc),
    },
    # 3. Buscar por nombre
    {
        "patrones": [r'buscar?\s+(["\w\s]+)', r'encontrar?\s+(["\w\s]+)', r'existe\s+(["\w\s]+)'],
        "builder": lambda m, doc: _tpl_buscar_nombre(m, doc),
    },
    # 4. Buscar por FL
    {
        "patrones": [r'FL[\s\-]?(\d+)', r'número\s+FL\s+(\d+)'],
        "builder": lambda m, doc: _tpl_buscar_fl(m, doc),
    },
    # 5. Buscar por CAS
    {
        "patrones": [r'CAS[\s\-]?(\d[\d\-]+)', r'número\s+CAS\s+([\d\-]+)'],
        "builder": lambda m, doc: _tpl_buscar_cas(m, doc),
    },
    # 6. Documentos cargados
    {
        "patrones": [r'documentos?\s+cargados?', r'qu[eé]\s+documentos?\s+hay', r'archivos?\s+indexados?'],
        "builder": lambda m, doc: _tpl_documentos(),
    },
]


def _tpl_contar_sustancias(doc):
    join, params = _where_doc(doc)
    sql = f"SELECT COUNT(*) AS total FROM sustancias s {join}"
    return sql.strip(), params

def _tpl_listar_todas(doc):
    join, params = _where_doc(doc)
    sql = f"SELECT s.nombre, s.identificador, s.datos, s.pagina FROM sustancias s {join} ORDER BY s.nombre LIMIT 200"
    return sql.strip(), params

def _tpl_buscar_nombre(match, doc):
    termino = match.group(1).strip().strip('"\'') if match.lastindex else ""
    join, params = _where_doc(doc)
    if join:
        where = join + " AND s.nombre LIKE %s"
    else:
        where = "WHERE s.nombre LIKE %s"
    sql = f"SELECT s.nombre, s.identificador, s.datos, s.pagina FROM sustancias s {where}"
    return sql.strip(), params + (f"%{termino}%",)

def _tpl_buscar_fl(match, doc):
    numero = match.group(1) if match.lastindex else ""
    join, params = _where_doc(doc)
    if join:
        where = join + " AND s.numero_fl = %s"
    else:
        where = "WHERE s.numero_fl = %s"
    sql = f"SELECT s.nombre, s.identificador, s.datos, s.pagina FROM sustancias s {where}"
    return sql.strip(), params + (numero,)

def _tpl_buscar_cas(match, doc):
    numero = match.group(1) if match.lastindex else ""
    join, params = _where_doc(doc)
    if join:
        where = join + " AND s.numero_cas = %s"
    else:
        where = "WHERE s.numero_cas = %s"
    sql = f"SELECT s.nombre, s.identificador, s.datos, s.pagina FROM sustancias s {where}"
    return sql.strip(), params + (numero,)

def _tpl_documentos():
    sql = "SELECT nombre, tipo, total_sustancias, fecha_ingesta FROM documentos ORDER BY fecha_ingesta DESC"
    return sql, ()


# ---------------------------------------------------------------------------
# Motor principal
# ---------------------------------------------------------------------------

def _intentar_template(pregunta: str, documento: str | None) -> tuple[str, tuple] | None:
    """Retorna (sql, params) si hay template match, None si no."""
    pregunta_lower = pregunta.lower()
    for tpl in TEMPLATES:
        for patron in tpl["patrones"]:
            m = re.search(patron, pregunta_lower, re.IGNORECASE)
            if m:
                logger.debug(f"SQL template match — patrón: {patron}")
                return tpl["builder"](m, documento)
    return None


def _validar_sql(sql: str) -> bool:
    """Solo permite SELECT. Rechaza cualquier operación de escritura."""
    sql_limpio = sql.strip().upper()
    if not sql_limpio.startswith("SELECT"):
        return False
    palabras_peligrosas = ["DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER", "CREATE", "GRANT"]
    return not any(p in sql_limpio for p in palabras_peligrosas)


def _gpt_genera_sql(pregunta: str, documento: str | None) -> tuple[str, tuple] | None:
    """Pide a GPT-4o que genere un SELECT basado en el schema."""
    cliente = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    schema = get_schema_info()

    filtro_nota = ""
    if documento and documento != "Todos los documentos":
        filtro_nota = f"\nEl usuario quiere filtrar por el documento: '{documento}'. Usa JOIN con documentos WHERE d.nombre = '{documento}'."

    system = (
        "Eres un experto en SQL. Genera ÚNICAMENTE un SELECT válido para MySQL "
        "basado en el schema proporcionado. No incluyas explicaciones, solo el SQL. "
        "No uses comillas invertidas innecesarias. Limita resultados a 200 filas máximo."
    )
    user = f"{schema}{filtro_nota}\n\nPregunta del usuario: {pregunta}\n\nSQL:"

    try:
        resp = cliente.chat.completions.create(
            model=MODELO,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=300,
            temperature=0,
        )
        sql = resp.choices[0].message.content.strip().rstrip(";")
        # Limpiar posibles bloques de código markdown
        sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"```", "", sql).strip()
        logger.debug(f"SQL generado por GPT: {sql}")
        return sql, ()
    except Exception as e:
        logger.error(f"SQL executor — error en GPT: {e}")
        return None


def ejecutar_analitico(pregunta: str, documento: str | None = None) -> dict:
    """
    Punto de entrada para queries analíticas.

    Retorna:
        {
            filas:    list[dict] | None,
            sql:      str,
            modo:     'template' | 'gpt' | 'error',
            mensaje:  str | None   (solo si hay error o MySQL no disponible)
        }
    """
    # Paso 1: template
    resultado = _intentar_template(pregunta, documento)
    modo = "template"

    # Paso 2: fallback GPT
    if resultado is None:
        logger.info("Sin template match — generando SQL con GPT")
        resultado = _gpt_genera_sql(pregunta, documento)
        modo = "gpt"

    if resultado is None:
        return {
            "filas": None,
            "sql": "",
            "modo": "error",
            "mensaje": "No se pudo generar una consulta SQL para esta pregunta.",
        }

    sql, params = resultado

    # Validación de seguridad
    if not _validar_sql(sql):
        logger.warning(f"SQL rechazado por validación: {sql}")
        return {
            "filas": None,
            "sql": sql,
            "modo": "error",
            "mensaje": "La consulta generada no es un SELECT válido y fue rechazada.",
        }

    filas = ejecutar_select(sql, params)

    if filas is None:
        return {
            "filas": None,
            "sql": sql,
            "modo": "error",
            "mensaje": "MySQL no está disponible. Verifica la configuración en .env.",
        }

    logger.info(f"SQL ejecutado ({modo}) — {len(filas)} filas retornadas")
    return {
        "filas": filas,
        "sql": sql,
        "modo": modo,
        "mensaje": None,
    }
