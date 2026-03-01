"""
router.py — Clasificación del tipo de consulta del usuario.

Retorna 'analitico' o 'semantico' para dirigir el flujo de respuesta.
"""

import re
from loguru import logger


PATRONES_ANALITICOS = [
    r'cu[aá]ntas?',
    r'listar?',
    r'contar',
    r'todas?\s+las?',
    r'cu[aá]les?\s+tienen',
    r'total\s+de',
    r'enumerar',
    r'qu[eé]\s+sustancias?',
    r'dame\s+todas?',
    r'lista\s+de',
    r'cuantos?',
    r'mostrar\s+todas?',
    r'todas?\s+los?',
]

# Patrones que indican referencia a una sustancia/identificador específico
PATRONES_ESPECIFICOS = [
    r'\bFL[\s\-]?\d+',
    r'\bCAS[\s\-]?\d+',
    r'\b\d{2,6}\-\d{2}\-\d{1}\b',   # formato CAS: 138-86-3
    r'\bartículo\s+\d+',
    r'\bconsidera[dn]do\s+\d+',
]


def clasificar_query(pregunta: str) -> str:
    """
    Clasifica la pregunta como 'analitico' o 'semantico'.

    Lógica:
    1. Si menciona un número FL, CAS o artículo específico → siempre 'semantico'
    2. Si coincide con patrones analíticos → 'analitico'
    3. Default → 'semantico'
    """
    pregunta_lower = pregunta.lower().strip()

    # Prioridad 1: referencia específica a identificador o artículo
    for patron in PATRONES_ESPECIFICOS:
        if re.search(patron, pregunta, re.IGNORECASE):
            logger.debug(f"Query clasificada como SEMANTICO (identificador específico): {pregunta[:60]}")
            return "semantico"

    # Prioridad 2: patrones analíticos (conteos, listados)
    for patron in PATRONES_ANALITICOS:
        if re.search(patron, pregunta_lower):
            logger.debug(f"Query clasificada como ANALITICO (patrón: {patron}): {pregunta[:60]}")
            return "analitico"

    logger.debug(f"Query clasificada como SEMANTICO (default): {pregunta[:60]}")
    return "semantico"
