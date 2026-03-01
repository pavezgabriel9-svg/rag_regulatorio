# Asistente Regulatorio — Guía de uso

Esta herramienta te permite **hacer preguntas en lenguaje natural** sobre documentos regulatorios (reglamentos de la Unión Europea, normativas internas, etc.) y obtener respuestas precisas con las fuentes indicadas.

No necesitas saber programación ni bases de datos para usarla.

Skills utilziados
- Brainstorming
- sql-optimizaion-patterns

---

## ¿Qué hace esta herramienta?

Imagina poder preguntarle directamente a un reglamento:

> *"¿Qué restricciones tiene el Limoneno?"*
> *"¿Cuántas sustancias tienen pureza definida?"*
> *"¿En qué artículo se habla de concentraciones máximas?"*

El asistente lee los documentos que tú cargues, los indexa, y luego responde a tus preguntas citando exactamente de dónde viene cada dato.

---

## Requisitos antes de comenzar

Necesitas tener instalado en tu computador:

- **Python 3.11 o superior** — el lenguaje en que está hecho el programa
  Descárgalo desde: [python.org/downloads](https://www.python.org/downloads/)
  Durante la instalación, marca la opción **"Add Python to PATH"**

- **Una API Key de OpenAI** — el servicio de inteligencia artificial que procesa las preguntas
  Cómo obtenerla: entra a [platform.openai.com](https://platform.openai.com), crea una cuenta, ve a *API Keys* y genera una nueva. Tiene costo por uso (muy bajo para este volumen).

- **Conexión a internet** — para comunicarse con OpenAI al hacer preguntas

---

## Instalación (solo la primera vez)

### Paso 1 — Configura tu API Key

1. Dentro de la carpeta `rag_regulatorio`, busca el archivo llamado `.env.example`
2. Copia ese archivo y renómbralo a `.env` (sin la palabra "example")
3. Abre el archivo `.env` con cualquier editor de texto (Bloc de notas, etc.)
4. Verás esta línea:
   ```
   OPENAI_API_KEY=sk-...
   ```
5. Reemplaza `sk-...` con tu API Key real. Por ejemplo:
   ```
   OPENAI_API_KEY=sk-proj-abc123xyz...
   ```
6. Guarda el archivo

> **Importante:** nunca compartas ese archivo `.env` con nadie. Contiene tu clave de acceso.

### Paso 2 — Inicia la aplicación

**En Windows:** haz doble clic en el archivo `iniciar.bat`

La primera vez instalará las dependencias automáticamente (puede tardar unos minutos). Luego se abrirá el navegador con la aplicación en `http://localhost:8501`.

**En Mac/Linux:** abre una terminal en la carpeta `rag_regulatorio` y ejecuta:
```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## Cómo usar la aplicación

La aplicación tiene dos secciones principales accesibles desde las pestañas superiores.

---

### Pestaña: Administración de Documentos

Aquí cargas los PDFs que quieres consultar.

#### Agregar un reglamento

1. Haz clic en **"Selecciona un PDF"** y elige el archivo desde tu computador
2. El sistema analizará automáticamente la estructura del documento:
   - Si es un texto corrido (artículos, considerandos), lo procesa directamente
   - Si tiene una tabla de sustancias, te pedirá que identifiques las columnas

3. **Si el documento tiene tabla:** aparecerá una pantalla para etiquetar cada columna con su categoría:

   | Categoría | ¿Qué va ahí? |
   |-----------|-------------|
   | **nombre** | El nombre de la sustancia |
   | **identificador** | Números FL, CAS u otros códigos |
   | **restriccion** | Límites o condiciones de uso |
   | **pureza** | Porcentaje mínimo de pureza |
   | **nota** | Observaciones adicionales |
   | **ignorar** | Columnas sin información relevante (ej. numeración) |

4. Haz clic en **"Procesar e indexar"**
5. Espera a que finalice — verás el número de fragmentos indexados al terminar

> El sistema detecta automáticamente en qué página comienza la tabla. Si no lo detecta bien, puedes ajustarlo manualmente con el número de página.

#### Ver documentos cargados

Debajo del formulario de carga verás una tabla con todos los documentos indexados:
- Nombre del archivo
- Número de fragmentos (chunks) y sustancias indexadas
- Fecha en que fue procesado

#### Eliminar un documento

1. Haz clic en **"Dar de baja"** junto al documento
2. Confirma la eliminación cuando te lo pida

Esto elimina el documento del sistema de búsqueda, pero **no borra el PDF de tu disco**.

---

### Pestaña: Chat Regulatorio

Aquí haces tus preguntas.

#### Cómo hacer preguntas

Escribe tu consulta en el campo de texto inferior y presiona Enter. El sistema detecta automáticamente si tu pregunta es:

- **Una consulta de información** (busca en el texto del reglamento):
  > *"¿Qué dice el artículo 3 sobre las restricciones?"*
  > *"¿Qué restricciones tiene el Citral?"*
  > *"¿Cuál es el límite de concentración para productos de enjuague?"*

- **Una consulta analítica** (cuenta, lista o compara datos de la tabla):
  > *"¿Cuántas sustancias tienen restricciones?"*
  > *"Lista todas las sustancias sin pureza definida"*
  > *"¿Cuántos ingredientes hay en total?"*

No necesitas usar comandos especiales — escribe en español natural.

#### Filtrar por documento

Si tienes varios reglamentos cargados, puedes limitar la búsqueda a uno específico usando el **selector del panel izquierdo** ("Filtrar por documento").

- **"Todos los documentos"** → busca en todos los reglamentos cargados
- **Nombre de un documento** → busca solo en ese reglamento

#### Entender la respuesta

Cada respuesta incluye:

- El texto con la respuesta a tu pregunta
- Una sección **"Fragmentos consultados"** (puedes expandirla) que muestra exactamente qué partes del documento se usaron para generar la respuesta
- Si aparece el badge **"relevancia baja"**, significa que los fragmentos encontrados no eran muy similares a tu pregunta — la respuesta puede ser menos precisa

---

## Panel de estado (barra lateral)

En el panel izquierdo verás siempre:

| Indicador | ¿Qué significa? |
|-----------|----------------|
| **ChromaDB** | Base de búsqueda semántica — debe estar ✅ activa siempre |
| **MySQL** | Base de datos analítica — opcional, permite consultas de conteo/listado más precisas |
| **API Key** | Tu clave de OpenAI — debe estar ✅ configurada |
| **Documentos** | Cantidad de PDFs indexados |

Si MySQL aparece como no disponible, la aplicación sigue funcionando correctamente para la mayoría de consultas.

---

## Tipos de documentos que soporta

El sistema detecta automáticamente tres tipos de estructura:

- **Solo texto** — reglamentos con artículos y párrafos (sin tabla)
- **Solo tabla** — anexos con listados de sustancias en formato tabla
- **Mixto** — reglamentos que combinan texto narrativo y una tabla de sustancias

Puedes cargar múltiples documentos de distintos tipos. Cada uno se mantiene identificado por separado.

---

## Solución de problemas frecuentes

| Problema | Qué hacer |
|----------|-----------|
| La aplicación no abre | Verifica que Python está instalado y que ejecutaste `iniciar.bat` como administrador |
| "API Key no configurada" | Revisa que el archivo `.env` existe y que la key está escrita correctamente (sin espacios antes ni después) |
| "No hay documentos indexados" | Ve a la pestaña Administración y carga al menos un PDF primero |
| La tabla no se detectó | Ajusta manualmente el número de página de inicio de la tabla en la UI |
| Las respuestas son lentas | Es normal — el sistema procesa el contexto completo para mayor precisión. Espera unos segundos |
| La respuesta dice "no encontré información" | Prueba reformular la pregunta o verifica que el documento correcto está seleccionado en el filtro |
| Error al cargar un PDF | Asegúrate de que el PDF no está protegido con contraseña y que no está abierto en otro programa |

---

## Preguntas frecuentes

**¿Mis documentos se envían a internet?**
El texto de los documentos se envía a OpenAI para generar los embeddings (representaciones matemáticas) y las respuestas. Los documentos no se almacenan en servidores de OpenAI más allá del tiempo de procesamiento de cada consulta.

**¿Puedo usar el sistema sin MySQL?**
Sí. MySQL es opcional. Sin él, el sistema usa solo la base vectorial (ChromaDB) y funciona bien para la mayoría de consultas. Solo las consultas de conteo y listado exacto pueden ser menos precisas.

**¿Qué pasa si cargo el mismo PDF dos veces?**
El sistema lo reconoce por nombre de archivo y reemplaza el documento anterior.

**¿Puedo cargar documentos en inglés o francés?**
Sí, el sistema puede procesar PDFs en otros idiomas, pero las respuestas se generarán en el idioma en que hagas la pregunta.

**¿El sistema tiene memoria de conversaciones anteriores?**
No. Cada sesión empieza desde cero. Los documentos indexados persisten, pero el historial de chat no.
