"""
extractor.py — Extracción de contenido PDF y análisis estructural.

Funciones principales:
  - analizar_estructura_pdf()   → detecta estructura del documento (tabla vs texto)
  - extraer_texto_narrativo()   → extrae artículos/párrafos como chunks
  - extraer_tabla_sustancias()  → extrae filas de tabla con mapeo de columnas
"""

import re
from pathlib import Path
from loguru import logger
import pdfplumber


# ---------------------------------------------------------------------------
# Análisis estructural
# ---------------------------------------------------------------------------

def _es_fila_numeracion(fila: list) -> bool:
    """
    Detecta si una fila es una fila de numeración de columnas
    (ej: ['1', '2', '3', '4'] o ['(1)', '(2)', ...]).
    """
    celdas = [str(c).strip() for c in fila if c is not None and str(c).strip()]
    if not celdas:
        return False
    return all(re.match(r'^\(?\d+\)?$', c) for c in celdas)


def _detectar_fila_cabecera(tabla: list) -> tuple[int, list[str]]:
    """
    Busca el índice de la fila real de cabecera en una tabla,
    saltando filas de numeración de columnas.

    Retorna (indice_cabecera, columnas_como_lista_de_str)
    """
    for i, fila in enumerate(tabla[:3]):  # buscar solo en las primeras 3 filas
        if _es_fila_numeracion(fila):
            continue
        # Verificar que la fila tenga contenido textual no trivial
        celdas = [str(c).strip() if c else "" for c in fila]
        if any(len(c) > 2 for c in celdas):
            return i, celdas
    # Fallback: usar la primera fila
    celdas = [str(c).strip() if c else f"Columna_{j}" for j, c in enumerate(tabla[0])]
    return 0, celdas


def analizar_estructura_pdf(pdf_path: str) -> dict:
    """
    Analiza el PDF y detecta qué páginas contienen tablas vs texto narrativo.
    Detección por estructura pura — no asume nombres de columnas específicos.

    Retorna:
        {
          "total_paginas": int,
          "paginas_texto": int,
          "paginas_tabla": int,
          "inicio_tabla": int | None,   # primera página con tabla (1-indexed)
          "columnas_detectadas": list[str],
          "preview_filas": list[list],  # primeras 3 filas para mostrar al usuario
          "confianza": float,
          "tipo_sugerido": str          # "solo_texto" | "solo_tabla" | "mixto"
        }
    """
    pdf_path = str(pdf_path)
    logger.info(f"Analizando estructura de: {pdf_path}")

    paginas_con_tabla = []
    paginas_solo_texto = []
    primera_pagina_tabla = None
    columnas_detectadas = []
    preview_filas = []

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)

        for i, page in enumerate(pdf.pages, start=1):
            tablas = page.extract_tables()
            tiene_tabla_util = False

            for tabla in tablas:
                if tabla and len(tabla) >= 2:
                    # Filtrar tablas de una sola columna (suelen ser numeración o headers)
                    num_cols = max(len(fila) for fila in tabla if fila)
                    if num_cols >= 2:
                        tiene_tabla_util = True

                        # Registrar primera tabla con sus columnas
                        if primera_pagina_tabla is None:
                            primera_pagina_tabla = i
                            # Detectar fila de cabecera real (saltando filas de numeración)
                            idx_cabecera, columnas_detectadas = _detectar_fila_cabecera(tabla)
                            # Guardar hasta 3 filas de datos (después de la cabecera)
                            datos = [
                                r for r in tabla[idx_cabecera + 1:]
                                if any(c for c in r if c)
                            ]
                            preview_filas = datos[:3]

            if tiene_tabla_util:
                paginas_con_tabla.append(i)
            else:
                paginas_solo_texto.append(i)

    n_tabla = len(paginas_con_tabla)
    n_texto = len(paginas_solo_texto)
    proporcion_tabla = n_tabla / total if total > 0 else 0

    # Confianza: alta si hay clara separación entre secciones
    if primera_pagina_tabla is not None:
        paginas_antes_tabla = primera_pagina_tabla - 1
        # Esperamos que las páginas de texto sean continuas al inicio
        texto_al_inicio = sum(1 for p in paginas_solo_texto if p < primera_pagina_tabla)
        confianza = min(1.0, 0.6 + (texto_al_inicio / max(paginas_antes_tabla, 1)) * 0.4)
    else:
        confianza = 0.95 if n_texto == total else 0.5

    # Solo clasificar como "puro" si literalmente no hay páginas del otro tipo
    if n_tabla == 0:
        tipo_sugerido = "solo_texto"
    elif n_texto == 0:
        tipo_sugerido = "solo_tabla"
    else:
        tipo_sugerido = "mixto"

    resultado = {
        "total_paginas": total,
        "paginas_texto": n_texto,
        "paginas_tabla": n_tabla,
        "inicio_tabla": primera_pagina_tabla,
        "columnas_detectadas": columnas_detectadas,
        "preview_filas": preview_filas,
        "confianza": round(confianza, 2),
        "tipo_sugerido": tipo_sugerido,
    }

    logger.info(
        f"Estructura detectada: {tipo_sugerido} | "
        f"{n_texto} pág. texto, {n_tabla} pág. tabla | "
        f"inicio_tabla={primera_pagina_tabla} | confianza={confianza:.0%}"
    )
    return resultado


# ---------------------------------------------------------------------------
# Extracción de texto narrativo (artículos y párrafos)
# ---------------------------------------------------------------------------

REGEX_ARTICULO = re.compile(r'^Art[íi]culo\s+\d+', re.IGNORECASE | re.MULTILINE)
MAX_TOKENS_CHUNK = 400  # límite aproximado en palabras (no tokens reales)
OVERLAP_PALABRAS = 90


def _palabras(texto: str) -> int:
    return len(texto.split())


def _split_con_overlap(texto: str, numero_articulo: str, pagina: int) -> list[dict]:
    """Divide un artículo largo en sub-chunks con overlap."""
    palabras = texto.split()
    chunks = []
    inicio = 0
    parte = 1

    while inicio < len(palabras):
        fin = min(inicio + MAX_TOKENS_CHUNK, len(palabras))
        fragmento = " ".join(palabras[inicio:fin])
        total_partes = max(2, (len(palabras) + MAX_TOKENS_CHUNK - 1) // MAX_TOKENS_CHUNK)
        chunks.append({
            "texto": fragmento,
            "tipo": "articulo",
            "numero_articulo": numero_articulo,
            "sub_chunk": f"{parte}/{total_partes}",
            "pagina": pagina,
        })
        parte += 1
        inicio += MAX_TOKENS_CHUNK - OVERLAP_PALABRAS
        if fin == len(palabras):
            break

    return chunks


def extraer_texto_narrativo(pdf_path: str, pagina_fin_texto: int = None) -> list[dict]:
    """
    Extrae texto narrativo (artículos, considerandos, párrafos) del PDF.

    Args:
        pdf_path:         ruta al PDF
        pagina_fin_texto: última página de texto narrativo (si se conoce la estructura)
                          Si es None, procesa todas las páginas sin tabla.

    Retorna lista de dicts:
        {texto, tipo, numero_articulo, sub_chunk, pagina}
    """
    pdf_path = str(pdf_path)
    logger.info(f"Extrayendo texto narrativo de: {pdf_path}")

    articulos = {}   # numero_articulo → {texto_acumulado, pagina_inicio}
    articulo_actual = None
    chunks_pagina = []  # páginas sin artículos detectados → chunk por página

    with pdfplumber.open(pdf_path) as pdf:
        paginas_a_procesar = pdf.pages
        if pagina_fin_texto is not None:
            paginas_a_procesar = pdf.pages[:pagina_fin_texto]

        for page in paginas_a_procesar:
            texto_pagina = page.extract_text() or ""
            lineas = texto_pagina.splitlines()
            pagina_num = page.page_number
            texto_sin_articulo = []

            for linea in lineas:
                match = REGEX_ARTICULO.match(linea.strip())
                if match:
                    # Nuevo artículo encontrado
                    numero = re.search(r'\d+', linea).group()
                    articulo_actual = numero
                    if numero not in articulos:
                        articulos[numero] = {"texto": linea, "pagina": pagina_num}
                    else:
                        articulos[numero]["texto"] += "\n" + linea
                elif articulo_actual:
                    articulos[articulo_actual]["texto"] += "\n" + linea
                else:
                    texto_sin_articulo.append(linea)

            # Si la página no tenía artículos, guardar como chunk de página
            if not articulo_actual and texto_sin_articulo:
                texto_completo = "\n".join(texto_sin_articulo).strip()
                if texto_completo:
                    chunks_pagina.append({
                        "texto": texto_completo,
                        "tipo": "texto",
                        "numero_articulo": "",
                        "sub_chunk": "1/1",
                        "pagina": pagina_num,
                    })

    # Convertir artículos a chunks (con split si son largos)
    resultado = []
    for numero, data in articulos.items():
        texto = data["texto"].strip()
        pagina = data["pagina"]
        if _palabras(texto) <= MAX_TOKENS_CHUNK:
            resultado.append({
                "texto": texto,
                "tipo": "articulo",
                "numero_articulo": numero,
                "sub_chunk": "1/1",
                "pagina": pagina,
            })
        else:
            resultado.extend(_split_con_overlap(texto, numero, pagina))

    resultado.extend(chunks_pagina)
    resultado.sort(key=lambda x: (x["pagina"], x["sub_chunk"]))

    logger.info(f"Texto narrativo: {len(resultado)} chunks extraídos")
    return resultado


# ---------------------------------------------------------------------------
# Extracción de tabla con mapeo de columnas
# ---------------------------------------------------------------------------

def extraer_tabla_sustancias(
    pdf_path: str,
    pagina_inicio: int,
    mapeo_columnas: dict[str, str],
) -> list[dict]:
    """
    Extrae filas de tabla a partir de pagina_inicio usando el mapeo de columnas
    definido por el usuario.

    Args:
        pdf_path:       ruta al PDF
        pagina_inicio:  primera página de la tabla (1-indexed)
        mapeo_columnas: {nombre_columna_original: categoria_semantica}
                        Ej: {"Substance name": "nombre", "ADI": "restriccion"}
                        Categorías: "nombre" | "identificador" | "restriccion" |
                                    "pureza" | "nota" | "ignorar"

    Retorna lista de dicts con claves normalizadas + raw.
    """
    pdf_path = str(pdf_path)
    logger.info(
        f"Extrayendo tabla desde pág. {pagina_inicio} con mapeo: {mapeo_columnas}"
    )

    filas_resultado = []
    cabecera_confirmada = None

    with pdfplumber.open(pdf_path) as pdf:
        paginas_tabla = pdf.pages[pagina_inicio - 1:]

        for page in paginas_tabla:
            tablas = page.extract_tables()
            pagina_num = page.page_number

            for tabla in tablas:
                if not tabla or len(tabla) < 2:
                    continue

                # Detectar la fila de cabecera real (saltando filas de numeración)
                idx_cabecera, cols_cabecera = _detectar_fila_cabecera(tabla)

                # Verificar si esta fila es la cabecera del documento (puede repetirse por página)
                es_cabecera_doc = any(col in mapeo_columnas for col in cols_cabecera)

                if cabecera_confirmada is None:
                    cabecera_confirmada = cols_cabecera

                # Las filas de datos son las que están después del header (y después de numeración)
                inicio_datos = idx_cabecera + 1 if es_cabecera_doc else 0
                # Saltar también filas de numeración al inicio
                filas_candidatas = tabla[inicio_datos:]
                filas_datos = [
                    f for f in filas_candidatas
                    if not _es_fila_numeracion(f)
                ]

                for fila in filas_datos:
                    if not any(c for c in fila if c):
                        continue  # fila vacía

                    # Construir dict raw con nombres de columna originales
                    raw = {}
                    for j, celda in enumerate(fila):
                        if j < len(cabecera_confirmada):
                            col_nombre = cabecera_confirmada[j]
                        else:
                            col_nombre = f"col_{j}"
                        raw[col_nombre] = _limpiar_celda(celda)

                    # Construir dict semántico usando el mapeo
                    semantico = _aplicar_mapeo(raw, mapeo_columnas)
                    semantico["pagina"] = pagina_num
                    semantico["_raw"] = raw

                    # Solo incluir filas con al menos un campo "nombre" no vacío
                    if semantico.get("nombre", "").strip():
                        filas_resultado.append(semantico)

    logger.info(f"Tabla: {len(filas_resultado)} filas extraídas")
    return filas_resultado


def _limpiar_celda(valor) -> str:
    if valor is None:
        return ""
    return re.sub(r'\s+', ' ', str(valor)).strip()


def _aplicar_mapeo(raw: dict, mapeo: dict[str, str]) -> dict:
    """
    Convierte un dict de columnas originales a categorías semánticas.
    Si hay múltiples columnas mapeadas a la misma categoría, se concatenan.
    """
    CATEGORIAS = ["nombre", "identificador", "restriccion", "pureza", "nota"]
    resultado = {cat: "" for cat in CATEGORIAS}

    for col_original, categoria in mapeo.items():
        if categoria == "ignorar":
            continue
        valor = raw.get(col_original, "")
        if not valor:
            continue
        if categoria in resultado:
            if resultado[categoria]:
                resultado[categoria] += " | " + valor
            else:
                resultado[categoria] = valor

    return resultado
