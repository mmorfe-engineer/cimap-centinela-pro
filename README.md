# CENTINELA PRO (Fase 1)

Paquete “llave en mano” para ejecutar monitoreo político **solo con GitHub Actions**.

## ¿Qué hace?
1. **Busca** información reciente en múltiples capas con Perplexity Sonar.
2. **Redacta** un informe con Mistral (`mistral-large-latest`).
3. **Entrega** por canales (Telegram, Gmail, Discord, Slack) y publica HTML en **GitHub Pages** (`gh-pages`).

## Metodología operativa (resumen)
CENTINELA PRO aplica directrices de búsqueda y verificación desde el archivo de configuración:
`config/monitor_noticias_multicapa_ve_v1_1.json`

### 1) Capas y submódulos
La búsqueda se organiza por capas temáticas (capas 1–10) y submódulos con:
- **Consultas sugeridas (query_templates)**
- **Reglas de selección (selection_rules)**
- **Foco geográfico o institucional**

Estas directrices se inyectan en el prompt de Perplexity y guían la extracción de hallazgos por capa.

### 2) Registro de fuentes (source_registry)
Se usa un registro de fuentes para:
- Etiquetar cada hallazgo por **source_type**
- Señalar **source_bias_risk**
- Registrar **authority_score**
- Indicar si requiere **cross_check**

El etiquetado es automático en `redactor.py` usando el dominio de la URL.

### 3) Señales sociales (SA0–SA4)
Las señales de redes sociales se clasifican con niveles de alerta:
- **SA0** descartado
- **SA1** señal no verificada
- **SA2** alerta en observación
- **SA3** alerta verosímil
- **SA4** confirmado

Las señales SA1–SA3 **nunca se redactan como hechos** y deben llevar advertencia.

### 4) Verificación y trazabilidad (SIFT)
El informe incluye:
- Sección explícita **SIFT** (Stop/Investigate/Find/Trace)
- Conteo de hallazgos con URL
- Conteo con archivo (Wayback / Archive.today)

### 5) Política de frescura
Se privilegian fuentes con fecha visible. Si una fuente no está fechada, solo se admite cuando es oficial y relevante.

## Configuración para no-coder

### 1) Cargar secretos
En el repositorio: **Settings → Secrets and variables → Actions → New repository secret**.

Crea estos secretos (usa solo los que necesites para canales):

- `PERPLEXITY_API_KEY` (obligatorio para búsqueda real)
- `MISTRAL_API_KEY` (obligatorio para redacción con Mistral)
- `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` (opcional)
- `DISCORD_WEBHOOK_URL` (opcional)
- `SLACK_WEBHOOK_URL` (opcional)
- `GMAIL_REMITENTE`, `GMAIL_APP_PASSWORD`, `GMAIL_DESTINATARIO` (opcional)

> El workflow usa automáticamente `GITHUB_TOKEN` interno de GitHub Actions.

### 2) Ejecutar manualmente
1. Ir a **Actions**.
2. Abrir workflow **CENTINELA PRO Fase 1**.
3. Clic en **Run workflow**.
4. (Opcional) colocar `horas_atras` para forzar ventana personalizada.

### 3) Horarios automáticos
Venezuela (VET, UTC-4) no usa horario de verano.
- 12:00 VET (UTC-4) → `16:00 UTC`
- 18:00 VET (UTC-4) → `22:00 UTC`

## Variables opcionales
- `CENTINELA_CONFIG_PATH` → ruta del JSON de directrices (por defecto: `config/monitor_noticias_multicapa_ve_v1_1.json`)
- `ARCHIVE_URLS` → activar archivado automático (1/0)
- `ARCHIVE_LIMIT` → máximo de URLs a archivar
- `ARCHIVE_TIMEOUT` → timeout del archivado

## GitHub Pages (operativa)
El sistema publica automáticamente:
- `index.html`
- `informes/<correlativo>.html`

en la rama `gh-pages`.

Para habilitar visualización web:
1. Ir a **Settings → Pages**.
2. En **Build and deployment**, seleccionar **Deploy from a branch**.
3. Branch: `gh-pages` y folder `/ (root)`.
4. Guardar.

Cuando corra el workflow, la rama `gh-pages` se creará/actualizará sola.

## Archivos principales
- `.github/workflows/centinela_pro.yml`
- `config/monitor_noticias_multicapa_ve_v1_1.json`
- `buscador.py`
- `redactor.py`
- `monitor.py`
- `entrega.py`
- `requirements.txt`
