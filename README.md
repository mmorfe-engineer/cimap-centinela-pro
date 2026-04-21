# CENTINELA PRO (Fase 1)

Paquete “llave en mano” para ejecutar monitoreo político **solo con GitHub Actions**.

## ¿Qué hace?
1. **Busca** información reciente en 3 capas con Perplexity Sonar.
2. **Redacta** un informe con Claude.
3. **Entrega** por canales (Telegram, Gmail, Discord, Slack) y publica HTML en **GitHub Pages** (`gh-pages`).

## Configuración para no-coder

### 1) Cargar secretos
En el repositorio: **Settings → Secrets and variables → Actions → New repository secret**.

Crea estos secretos (usa solo los que necesites para canales):

- `PERPLEXITY_API_KEY` (obligatorio para búsqueda real)
- `ANTHROPIC_API_KEY` (obligatorio para redacción con Claude)
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
- `buscador.py`
- `redactor.py`
- `monitor.py`
- `entrega.py`
- `requirements.txt`
