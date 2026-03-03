"""
app.py — Interfaz Streamlit del Asistente Regulatorio.

Pestañas:
  1. Chat Regulatorio
  2. Administración de Documentos
"""

import os
import shutil
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from loguru import logger

# Módulos propios
from ingesta.extractor import analizar_estructura_pdf, extraer_texto_narrativo, extraer_tabla_sustancias
from ingesta.chunker import chunks_texto_a_documentos, chunks_tabla_a_documentos
from ingesta.ingest import indexar_documentos, listar_documentos, eliminar_documento, estado_sistema
from motor.router import clasificar_query
from motor.retriever import buscar
from motor.generator import generar_respuesta, analizar_consulta_analitica, formatear_resultado_sql
from motor.sql_executor import ejecutar_analitico
from motor import sql_manager

load_dotenv()

ARCHIVOS_DIR = Path(__file__).parent / "archivos"
ARCHIVOS_DIR.mkdir(exist_ok=True)

st.set_page_config(
    page_title="Asistente Regulatorio",
    page_icon="📋",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Estado de sesión
# ---------------------------------------------------------------------------

if "mensajes" not in st.session_state:
    st.session_state.mensajes = []
if "documento_filtro" not in st.session_state:
    st.session_state.documento_filtro = "Todos los documentos"
if "confirmar_baja" not in st.session_state:
    st.session_state.confirmar_baja = {}
if "upload_key" not in st.session_state:
    st.session_state.upload_key = 0


# ---------------------------------------------------------------------------
# Sidebar — Estado del sistema
# ---------------------------------------------------------------------------

def render_sidebar():
    with st.sidebar:
        st.title("📋 Asistente Regulatorio")
        st.divider()

        estado = estado_sistema()
        estado_sql = sql_manager.estado_mysql()
        api_gpt = os.getenv("OPENAI_API_KEY")

        icono_db = "🟢" if estado["conectado"] else "🔴"
        icono_sql = "🟢" if estado_sql["conectado"] else "🔴"
        icono_api = "🟢" if api_gpt else "🔴"

        st.write(f"{icono_db} **ChromaDB:** {'conectado' if estado['conectado'] else 'error'}")
        st.write(f"{icono_sql} **MySQL:** {'conectado' if estado_sql['conectado'] else 'no disponible'}")
        st.write(f"{icono_api} **API GPT:** {'conectado' if api_gpt else 'no disponible'}")
        st.write(f"📄 **Documentos:** {estado['total_documentos']}")
        st.write(f"🧩 **Fragmentos ChromaDB:** {estado['total_chunks']}")
        st.write(f"🗄️ **Sustancias en SQL:** {estado_sql['total_sustancias']}")

        st.divider()

        # Filtro por documento en el chat
        docs = listar_documentos()
        nombres_docs = ["Todos los documentos"] + [d["nombre"] for d in docs]
        st.session_state.documento_filtro = st.selectbox(
            "🔍 Buscar en:",
            nombres_docs,
            index=nombres_docs.index(st.session_state.documento_filtro)
            if st.session_state.documento_filtro in nombres_docs
            else 0,
        )

        st.divider()

        if st.button("🗑️ Limpiar conversación", use_container_width=True):
            st.session_state.mensajes = []
            st.rerun()


# ---------------------------------------------------------------------------
# Pestaña 1 — Chat Regulatorio
# ---------------------------------------------------------------------------

def render_chat():
    st.header("💬 Chat Regulatorio")
    st.caption("Consulta los reglamentos indexados en lenguaje natural")

    docs = listar_documentos()
    if not docs:
        st.info(
            "No hay documentos indexados. Ve a la pestaña **Administración** para agregar un reglamento.",
            icon="📂",
        )
        return

    # Historial de mensajes
    for mensaje in st.session_state.mensajes:
        with st.chat_message(mensaje["rol"]):
            st.markdown(mensaje["contenido"])

            if mensaje["rol"] == "assistant" and "respuesta_data" in mensaje:
                _render_fuentes(mensaje["respuesta_data"], mensaje.get("tipo_query", "semantico"))

    # Input del usuario
    pregunta = st.chat_input("Escribe tu consulta regulatoria...")
    if not pregunta:
        return

    # Mostrar pregunta
    with st.chat_message("user"):
        st.markdown(pregunta)
    st.session_state.mensajes.append({"rol": "user", "contenido": pregunta})

    # Procesar y responder
    with st.chat_message("assistant"):
        with st.spinner("Buscando en los reglamentos..."):
            try:
                documento_filtro = st.session_state.documento_filtro
                tipo_query = clasificar_query(pregunta)
                estado_sql = sql_manager.estado_mysql()

                if tipo_query == "analitico" and estado_sql["conectado"]:
                    # Ruta analítica: MySQL
                    resultado_sql = ejecutar_analitico(pregunta, documento_filtro)
                    if resultado_sql["mensaje"]:
                        # Error o MySQL no disponible → fallback a ChromaDB
                        chunks = buscar(
                            query=pregunta,
                            documento=documento_filtro if documento_filtro != "Todos los documentos" else None,
                        )
                        respuesta_data = analizar_consulta_analitica(pregunta, chunks) if chunks else {
                            "respuesta": resultado_sql["mensaje"],
                            "fuentes": [], "tipo_query": "analitico", "chunks_usados": 0,
                        }
                    else:
                        respuesta_data = formatear_resultado_sql(
                            pregunta,
                            resultado_sql["filas"],
                            resultado_sql["sql"],
                            resultado_sql["modo"],
                        )
                else:
                    # Ruta semántica (o analítica sin MySQL disponible)
                    chunks = buscar(
                        query=pregunta,
                        documento=documento_filtro if documento_filtro != "Todos los documentos" else None,
                    )
                    if not chunks:
                        respuesta_data = {
                            "respuesta": "No encontré fragmentos relevantes en los documentos indexados. "
                                         "Intenta reformular la consulta.",
                            "fuentes": [],
                            "tipo_query": tipo_query,
                            "chunks_usados": 0,
                        }
                    elif tipo_query == "analitico":
                        respuesta_data = analizar_consulta_analitica(pregunta, chunks)
                    else:
                        respuesta_data = generar_respuesta(pregunta, chunks)

                st.markdown(respuesta_data["respuesta"])
                _render_fuentes(respuesta_data, respuesta_data["tipo_query"])

                st.session_state.mensajes.append({
                    "rol": "assistant",
                    "contenido": respuesta_data["respuesta"],
                    "respuesta_data": respuesta_data,
                    "tipo_query": respuesta_data["tipo_query"],
                })

            except Exception as e:
                st.error(f"Error al procesar la consulta: {e}")
                logger.exception(e)


def _render_fuentes(respuesta_data: dict, tipo_query: str):
    """Renderiza el badge de tipo y el expander de fragmentos o tabla SQL."""
    if tipo_query == "analitico_sql":
        modo = respuesta_data.get("modo_sql", "")
        modo_label = "template" if modo == "template" else "GPT"
        st.caption(f"🗄️ Consulta SQL ({modo_label})")
        filas = respuesta_data.get("filas_sql", [])
        if filas:
            with st.expander(f"📋 Datos de MySQL ({len(filas)} filas)", expanded=False):
                import pandas as pd
                st.dataframe(pd.DataFrame(filas), use_container_width=True)
                st.caption(f"`{respuesta_data.get('sql_usado', '')}`")
        return

    if tipo_query == "analitico":
        st.caption("📊 Consulta analítica (ChromaDB)")
    else:
        st.caption("🔍 Búsqueda semántica")

    fuentes = respuesta_data.get("fuentes", []) if isinstance(respuesta_data, dict) else respuesta_data
    if not fuentes:
        return

    with st.expander(f"📎 Fragmentos consultados ({len(fuentes)})", expanded=False):
        for f in fuentes:
            articulo_info = ""
            if f.get("numero_articulo"):
                articulo_info = f" · Art. {f['numero_articulo']}"
                if f.get("sub_chunk") and f["sub_chunk"] != "1/1":
                    articulo_info += f" ({f['sub_chunk']})"

            score = f.get("score", 0)
            baja_relevancia = f.get("relevancia_baja", False)
            score_label = f"Score: {score:.2f}"
            if baja_relevancia:
                score_label += "  ⚠️ *relevancia baja*"

            st.markdown(
                f"**[{f['numero']}]** {f['documento']} — {f['tipo']}{articulo_info} · pág. {f['pagina']}"
            )
            st.caption(score_label)
            st.markdown(f"> {f['preview']}")
            st.divider()


# ---------------------------------------------------------------------------
# Pestaña 2 — Administración de Documentos
# ---------------------------------------------------------------------------

def render_administracion():
    st.header("⚙️ Administración de Documentos")

    _seccion_documentos_indexados()
    st.divider()
    _seccion_agregar_documento()


def _seccion_documentos_indexados():
    st.subheader("Documentos indexados")
    docs = listar_documentos()

    if not docs:
        st.info("No hay documentos indexados aún.", icon="📂")
        return

    for doc in docs:
        col1, col2, col3, col4, col5, col6 = st.columns([3, 1, 1, 1, 2, 1])
        col1.write(f"📄 **{doc['nombre']}**")
        col2.write(f"{doc['total_chunks']} chunks")
        col3.write(f"{doc['articulos']} art.")
        col4.write(f"{doc['sustancias']} sust.")
        fecha = doc['fecha_ingesta'][:10] if doc['fecha_ingesta'] else "—"
        col5.write(fecha)

        nombre = doc["nombre"]
        if col6.button("🗑️ Dar de baja", key=f"baja_{nombre}"):
            st.session_state.confirmar_baja[nombre] = True

        if st.session_state.confirmar_baja.get(nombre):
            st.warning(
                f"¿Confirmas que deseas eliminar **{nombre}** del sistema? "
                "Esto no elimina el PDF del disco.",
                icon="⚠️",
            )
            c1, c2 = st.columns(2)
            if c1.button("✅ Sí, eliminar", key=f"confirm_{nombre}"):
                eliminados = eliminar_documento(nombre)
                st.success(f"✅ {nombre} eliminado correctamente. Se eliminaron {eliminados} fragmentos.")
                st.session_state.confirmar_baja[nombre] = False
                st.rerun()
            if c2.button("Cancelar", key=f"cancel_{nombre}"):
                st.session_state.confirmar_baja[nombre] = False
                st.rerun()


def _seccion_agregar_documento():
    st.subheader("Agregar nuevo documento")

    archivo = st.file_uploader("Selecciona un PDF", type=["pdf"], key=f"uploader_{st.session_state.upload_key}")
    if not archivo:
        return

    nombre_archivo = archivo.name

    # Guardar PDF en ./archivos/
    ruta_pdf = ARCHIVOS_DIR / nombre_archivo
    if not ruta_pdf.exists():
        with open(ruta_pdf, "wb") as f:
            shutil.copyfileobj(archivo, f)

    # --- Paso 1: Análisis estructural ---
    st.divider()
    st.markdown("### 📊 Análisis del documento")

    if "analisis_pdf" not in st.session_state or st.session_state.get("analisis_nombre") != nombre_archivo:
        with st.spinner("Analizando estructura del PDF..."):
            analisis = analizar_estructura_pdf(str(ruta_pdf))
            st.session_state.analisis_pdf = analisis
            st.session_state.analisis_nombre = nombre_archivo

    analisis = st.session_state.analisis_pdf
    tipo = analisis["tipo_sugerido"]
    confianza = analisis["confianza"]

    # Mostrar resumen
    col1, col2, col3 = st.columns(3)
    col1.metric("Total páginas", analisis["total_paginas"])
    col2.metric("Páginas de texto", analisis["paginas_texto"])
    col3.metric("Páginas de tabla", analisis["paginas_tabla"])

    if confianza < 0.7:
        st.warning(
            f"⚠️ La detección de estructura tiene confianza baja ({confianza:.0%}). "
            "Revisa y ajusta los parámetros manualmente.",
            icon="⚠️",
        )
    else:
        st.success(f"Estructura detectada: **{tipo}** · confianza {confianza:.0%}", icon="✅")

    # Opción de indexar como texto plano para documentos con tablas complejas
    forzar_solo_texto = False
    if tipo == "mixto":
        forzar_solo_texto = st.checkbox(
            "📄 Indexar todo como texto (sin extraer tabla estructurada)",
            value=False,
            help=(
                "Útil para documentos con múltiples tablas intercaladas, tablas de medición "
                "que no son listas de sustancias, o documentos tipo monografía donde cada "
                "sustancia ocupa varias páginas. El sistema extraerá el texto de todas las "
                "páginas y lo hará buscable semánticamente."
            ),
        )
        if forzar_solo_texto:
            tipo = "solo_texto"

    # Ajuste manual del inicio de tabla
    inicio_tabla_ajustado = analisis.get("inicio_tabla")
    if tipo in ("mixto", "solo_tabla") and inicio_tabla_ajustado is not None:
        inicio_tabla_ajustado = st.number_input(
            "✏️ Ajustar página de inicio de la tabla:",
            min_value=1,
            max_value=analisis["total_paginas"],
            value=analisis["inicio_tabla"],
            step=1,
        )

    # --- Paso 2: Mapeo de columnas (si hay tabla) ---
    mapeo_columnas = {}
    if tipo in ("mixto", "solo_tabla") and analisis.get("columnas_detectadas"):
        st.divider()
        st.markdown("### 🗂️ Mapeo de columnas de la tabla")

        if analisis.get("preview_filas"):
            st.markdown("**Vista previa de las primeras filas detectadas:**")
            st.dataframe(
                _preview_a_dataframe(analisis["columnas_detectadas"], analisis["preview_filas"]),
                use_container_width=True,
            )

        CATEGORIAS_LABELS = {
            "nombre": "Nombre principal (obligatorio — al menos 1)",
            "identificador": "Identificador (CAS, FL u otro código)",
            "datos": "Incluir como datos",
            "ignorar": "Ignorar",
        }

        st.markdown("**Etiqueta cada columna detectada:**")
        st.caption(
            "Asigna **Nombre principal** a la columna con el nombre de la entidad. "
            "**Identificador** para códigos CAS, FL, etc. "
            "**Incluir como datos** para el resto de columnas relevantes. "
            "**Ignorar** para columnas sin información útil (numeración, etc.)."
        )
        for i, col in enumerate(analisis["columnas_detectadas"]):
            key = f"mapeo_{nombre_archivo}_{col}"
            # Primera columna: default "nombre"; resto: default "datos"
            default_idx = 0 if i == 0 else 2
            opcion = st.selectbox(
                f'Columna: **"{col}"**',
                options=list(CATEGORIAS_LABELS.keys()),
                format_func=lambda x: CATEGORIAS_LABELS[x],
                index=default_idx,
                key=key,
            )
            mapeo_columnas[col] = opcion

        if not any(v == "nombre" for v in mapeo_columnas.values()):
            st.warning("Asigna al menos una columna como **Nombre principal** para poder indexar.", icon="⚠️")

    # --- Paso 3: Procesar e indexar ---
    st.divider()
    puede_procesar = (
        tipo == "solo_texto" or
        (tipo in ("mixto", "solo_tabla") and any(v == "nombre" for v in mapeo_columnas.values()))
    )

    if st.button("⬆️ Procesar e indexar", disabled=not puede_procesar, type="primary"):
        _procesar_e_indexar(
            ruta_pdf=str(ruta_pdf),
            nombre_archivo=nombre_archivo,
            analisis=analisis,
            inicio_tabla=inicio_tabla_ajustado,
            mapeo_columnas=mapeo_columnas,
            tipo=tipo,
        )


def _procesar_e_indexar(
    ruta_pdf: str,
    nombre_archivo: str,
    analisis: dict,
    inicio_tabla,
    mapeo_columnas: dict,
    tipo: str,
):
    """Ejecuta el pipeline completo de ingesta con barra de progreso."""
    progress = st.progress(0, text="Iniciando...")
    total_chunks = 0
    todos_los_docs = []
    filas_tabla_sql = []

    try:
        # Paso 1: Extraer texto narrativo
        if tipo in ("solo_texto", "mixto"):
            progress.progress(10, text="📄 Extrayendo texto narrativo...")
            if tipo == "solo_texto":
                # Modo texto puro: procesar TODAS las páginas sin exclusiones.
                # Cubre documentos tipo monografía (múltiples tablas intercaladas)
                # donde las "tablas" no son listas de sustancias sino especificaciones.
                chunks_texto = extraer_texto_narrativo(ruta_pdf)
            else:
                # Modo mixto: excluir páginas con tabla para no duplicar su contenido.
                paginas_tabla = set(analisis.get("paginas_con_tabla", []))
                chunks_texto = extraer_texto_narrativo(ruta_pdf, paginas_a_excluir=paginas_tabla or None)
            todos_los_docs.extend(chunks_texto_a_documentos(chunks_texto, nombre_archivo))

        # Paso 2: Extraer tabla
        if tipo in ("solo_tabla", "mixto") and mapeo_columnas and inicio_tabla:
            progress.progress(30, text="📊 Procesando tabla de sustancias...")
            filas_tabla_sql = extraer_tabla_sustancias(ruta_pdf, inicio_tabla, mapeo_columnas)
            todos_los_docs.extend(chunks_tabla_a_documentos(filas_tabla_sql, nombre_archivo))

        if not todos_los_docs:
            st.error("No se pudo extraer contenido del PDF. Revisa el archivo.")
            progress.empty()
            return

        # Paso 3: Generar embeddings e indexar en ChromaDB + MySQL
        progress.progress(60, text="🔢 Generando embeddings...")
        total_chunks = indexar_documentos(
            todos_los_docs,
            nombre_archivo,
            mapeo_columnas,
            filas_tabla=filas_tabla_sql,
            tipo_documento=tipo,
        )

        progress.progress(90, text="💾 Guardando en base de conocimiento...")
        progress.progress(100, text="")
        progress.empty()

        st.success(f"✅ Listo. Se indexaron **{total_chunks}** fragmentos de {nombre_archivo}.")

        # Resetear el file_uploader y limpiar análisis para el próximo archivo
        st.session_state.upload_key += 1
        for key in ("analisis_pdf", "analisis_nombre"):
            st.session_state.pop(key, None)

        st.rerun()

    except Exception as e:
        progress.empty()
        st.error(f"Error durante la indexación: {e}")
        logger.exception(e)


def _preview_a_dataframe(columnas: list, filas: list):
    """Convierte datos de preview a DataFrame de pandas para st.dataframe."""
    import pandas as pd
    datos = []
    for fila in filas:
        fila_dict = {}
        for j, col in enumerate(columnas):
            valor = fila[j] if j < len(fila) else ""
            fila_dict[col] = valor if valor else ""
        datos.append(fila_dict)
    return pd.DataFrame(datos, columns=columnas)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

@st.cache_resource
def _init_mysql():
    """Crea las tablas MySQL una sola vez por sesión de servidor."""
    sql_manager.init_db()

def main():
    _init_mysql()
    render_sidebar()

    tab1, tab2 = st.tabs(["💬 Chat Regulatorio", "⚙️ Administración de Documentos"])

    with tab1:
        render_chat()

    with tab2:
        render_administracion()


if __name__ == "__main__":
    main()
