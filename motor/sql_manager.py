"""
sql_manager.py — Conexión MySQL y operaciones CRUD para sustancias.

Tablas gestionadas:
  - documentos: metadata de cada PDF indexado
  - sustancias:  filas de tabla extraídas, con campos semánticos + raw_json

Si las variables MYSQL_* no están configuradas, todas las funciones retornan
valores vacíos/None sin lanzar excepción (graceful degradation).
"""

import json
import os
from datetime import datetime

import pymysql
import pymysql.cursors
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

DDL = """
CREATE TABLE IF NOT EXISTS documentos (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    nombre           VARCHAR(255) NOT NULL UNIQUE,
    tipo             VARCHAR(20)  NOT NULL,
    total_sustancias INT          DEFAULT 0,
    fecha_ingesta    DATETIME     NOT NULL,
    INDEX idx_nombre (nombre)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sustancias (
    id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    documento_id   INT UNSIGNED NOT NULL,
    nombre         VARCHAR(500) NOT NULL,
    identificador  VARCHAR(255),
    numero_fl      VARCHAR(50),
    numero_cas     VARCHAR(100),
    restriccion    TEXT,
    pureza         VARCHAR(255),
    nota           TEXT,
    pagina         SMALLINT UNSIGNED,
    raw_json       JSON,
    FOREIGN KEY (documento_id)
        REFERENCES documentos(id) ON DELETE CASCADE,
    INDEX idx_documento      (documento_id),
    INDEX idx_nombre         (nombre(100)),
    INDEX idx_doc_nombre     (documento_id, nombre(100)),
    INDEX idx_fl             (numero_fl),
    INDEX idx_cas            (numero_cas),
    INDEX idx_pagina         (pagina)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def _get_config() -> dict | None:
    """Retorna config de conexión o None si faltan variables."""
    host = os.getenv("MYSQL_HOST")
    user = os.getenv("MYSQL_USER")
    password = os.getenv("MYSQL_PASSWORD")
    database = os.getenv("MYSQL_DATABASE", "rag_regulatorio")
    if not all([host, user, password]):
        return None
    return {
        "host": host,
        "port": int(os.getenv("MYSQL_PORT", 3306)),
        "user": user,
        "password": password,
        "database": database,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": True,
    }


def _conectar() -> pymysql.Connection | None:
    """Abre una conexión. Retorna None si no hay config o si falla."""
    config = _get_config()
    if config is None:
        return None
    try:
        return pymysql.connect(**config)
    except Exception as e:
        logger.error(f"MySQL — error de conexión: {e}")
        return None


def init_db() -> bool:
    """
    Crea las tablas si no existen.
    Retorna True si OK, False si MySQL no disponible.
    """
    conn = _conectar()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            for statement in DDL.strip().split(";"):
                stmt = statement.strip()
                if stmt:
                    cur.execute(stmt)
        logger.info("MySQL — tablas verificadas/creadas")
        return True
    except Exception as e:
        logger.error(f"MySQL — error en init_db: {e}")
        return False
    finally:
        conn.close()


def estado_mysql() -> dict:
    """Métricas básicas para el sidebar."""
    conn = _conectar()
    if conn is None:
        return {"conectado": False, "total_documentos": 0, "total_sustancias": 0}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM documentos")
            total_docs = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM sustancias")
            total_sus = cur.fetchone()["n"]
        return {
            "conectado": True,
            "total_documentos": total_docs,
            "total_sustancias": total_sus,
        }
    except Exception as e:
        logger.error(f"MySQL — error en estado_mysql: {e}")
        return {"conectado": False, "total_documentos": 0, "total_sustancias": 0}
    finally:
        conn.close()


def insertar_documento(nombre: str, tipo: str) -> int | None:
    """
    Inserta o actualiza un documento. Retorna el documento_id.
    Si el documento ya existe, retorna su id existente.
    """
    conn = _conectar()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documentos (nombre, tipo, fecha_ingesta)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE tipo=%s, fecha_ingesta=%s
                """,
                (nombre, tipo, datetime.now(), tipo, datetime.now()),
            )
            cur.execute("SELECT id FROM documentos WHERE nombre=%s", (nombre,))
            row = cur.fetchone()
            return row["id"] if row else None
    except Exception as e:
        logger.error(f"MySQL — error en insertar_documento: {e}")
        return None
    finally:
        conn.close()


def insertar_sustancias(documento_id: int, filas: list[dict]) -> int:
    """
    Inserta las filas de tabla en MySQL en batch.
    Cada fila es un dict con claves semánticas + _raw del extractor.

    Retorna número de filas insertadas.
    """
    if not filas or documento_id is None:
        return 0

    conn = _conectar()
    if conn is None:
        return 0

    registros = []
    for fila in filas:
        raw = fila.get("_raw", {})
        identificador = fila.get("identificador", "") or ""
        registros.append((
            documento_id,
            (fila.get("nombre") or "")[:500],
            identificador[:255],
            _extraer_fl(identificador),
            _extraer_cas(identificador),
            fila.get("restriccion") or None,
            (fila.get("pureza") or None),
            fila.get("nota") or None,
            fila.get("pagina") or None,
            json.dumps(raw, ensure_ascii=False) if raw else None,
        ))

    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO sustancias
                    (documento_id, nombre, identificador, numero_fl, numero_cas,
                     restriccion, pureza, nota, pagina, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                registros,
            )
            # Actualizar contador en documentos
            cur.execute(
                "UPDATE documentos SET total_sustancias=%s WHERE id=%s",
                (len(registros), documento_id),
            )
        logger.info(f"MySQL — {len(registros)} sustancias insertadas (doc_id={documento_id})")
        return len(registros)
    except Exception as e:
        logger.error(f"MySQL — error en insertar_sustancias: {e}")
        return 0
    finally:
        conn.close()


def eliminar_documento(nombre: str) -> int:
    """
    Elimina documento y sus sustancias (CASCADE).
    Retorna número de sustancias eliminadas.
    """
    conn = _conectar()
    if conn is None:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM documentos WHERE nombre=%s", (nombre,))
            row = cur.fetchone()
            if not row:
                return 0
            doc_id = row["id"]
            cur.execute("SELECT COUNT(*) AS n FROM sustancias WHERE documento_id=%s", (doc_id,))
            n = cur.fetchone()["n"]
            cur.execute("DELETE FROM documentos WHERE id=%s", (doc_id,))
        logger.info(f"MySQL — documento '{nombre}' eliminado ({n} sustancias)")
        return n
    except Exception as e:
        logger.error(f"MySQL — error en eliminar_documento: {e}")
        return 0
    finally:
        conn.close()


def listar_documentos_sql() -> list[dict]:
    """Lista documentos con sustancias indexadas en MySQL."""
    conn = _conectar()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT nombre, tipo, total_sustancias, fecha_ingesta FROM documentos ORDER BY fecha_ingesta DESC"
            )
            return cur.fetchall()
    except Exception as e:
        logger.error(f"MySQL — error en listar_documentos_sql: {e}")
        return []
    finally:
        conn.close()


def ejecutar_select(sql: str, params: tuple = ()) -> list[dict] | None:
    """
    Ejecuta un SELECT parametrizado y retorna las filas.
    Retorna None si hay error de conexión.
    """
    conn = _conectar()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except Exception as e:
        logger.error(f"MySQL — error ejecutando SELECT: {e}")
        return []
    finally:
        conn.close()


def get_schema_info() -> str:
    """Retorna descripción del schema para usar en el prompt de GPT."""
    return """
Tablas disponibles en MySQL:

documentos(id, nombre, tipo, total_sustancias, fecha_ingesta)
  - nombre: nombre del archivo PDF
  - tipo: 'mixto' | 'solo_tabla' | 'solo_texto'
  - total_sustancias: cantidad de sustancias indexadas

sustancias(id, documento_id, nombre, identificador, numero_fl, numero_cas,
           restriccion, pureza, nota, pagina, raw_json)
  - nombre: nombre de la sustancia
  - identificador: texto completo (ej. "CAS 138-86-3 | FL 491")
  - numero_fl: número FL extraído (ej. "491")
  - numero_cas: número CAS extraído (ej. "138-86-3")
  - restriccion: restricciones de uso (puede ser NULL o vacío)
  - pureza: pureza mínima requerida (puede ser NULL o vacío)
  - nota: nota regulatoria adicional
  - pagina: número de página en el PDF

Relación: sustancias.documento_id → documentos.id
""".strip()


# ---------------------------------------------------------------------------
# Helpers de extracción
# ---------------------------------------------------------------------------

def _extraer_fl(identificador: str) -> str | None:
    import re
    if not identificador:
        return None
    m = re.search(r'FL[\s\-]?(\d+)', identificador, re.IGNORECASE)
    return m.group(1) if m else None


def _extraer_cas(identificador: str) -> str | None:
    import re
    if not identificador:
        return None
    m = re.search(r'(\d{2,7}-\d{2}-\d{1})', identificador)
    return m.group(1) if m else None
