# CENTINELA PRO

Sistema de monitoreo político de Venezuela, automatizado con **GitHub Actions**.
Produce informes de inteligencia política estructurados, dos veces al día, con
distribución multicanal y memoria entre cortes.

---

## ¿Qué hace?

1. **Busca** (`buscador.py`) información reciente con Perplexity Sonar, en 15 llamadas
   focalizadas por submódulo estratégico, inyectando toda la metodología del JSON v1.1
   y el catálogo de actores.
2. **Redacta** (`redactor.py`) un informe HTML responsivo con Mistral, estructurado en
   las 11 secciones canónicas del `output_contract`, con semáforos visuales A0-A4.
3. **Compara** (`estado_pipeline.py`) el corte actual contra el anterior para producir
   la sección "Novedades vs. corte anterior".
4. **Entrega** (`entrega.py`) por Telegram, Discord, Slack y Gmail, y publica el HTML
   completo en **GitHub Pages**.

---

## Arquitectura del pipeline (Fases A–D)

```
monitor.py (orquestador)
   │
   ├─ 1) buscador.buscar_noticias()          → 15 calls Perplexity por submódulo
   ├─ 2) estado_pipeline.cargar_snapshot()   → lee corte anterior (rama 'estado')
   ├─ 3) estado_pipeline.detectar_novedades()→ diff actual vs anterior
   ├─ 4) redactor.redactar_informe()         → HTML + texto + headlines (Mistral)
   ├─ 5) entrega.entregar_informe()          → Telegram/Discord/Slack/Gmail/Pages
   └─ 6) estado_pipeline.guardar_snapshot()  → guarda corte actual (rama 'estado')
```

El componente de estado (Fase D) es **no crítico**: si falla la lectura o escritura del
snapshot, el pipeline continúa y produce el informe igual, solo que sin la sección de
novedades. Un fallo de memoria nunca tumba la entrega.

---

## Sistemas de clasificación

### Semáforo A0-A4 (urgencia + impacto decisional) — todos los hallazgos
| Nivel | Color | Significado |
|-------|-------|-------------|
| A4 | 🔴 | Alerta prioritaria (respuesta 0-3h) |
| A3 | 🟠 | Señal estratégica |
| A2 | 🟡 | Narrativa relevante |
| A1 | 🟢 | Reacción menor |
| A0 | ⚪ | Actividad rutinaria |

### SA0-SA4 (verificación) — solo señales sociales / Telegram
| Nivel | Significado |
|-------|-------------|
| SA4 | Confirmado por fuente primaria |
| SA3 | Verosímil (OSINT / cobertura secundaria) |
| SA2 | En observación (sin confirmación primaria) |
| SA1 | No verificada (no tratar como hecho) |
| SA0 | Descartada |

Las señales SA1-SA3 nunca se redactan como hechos; llevan leyenda de advertencia.

### Estado general del día (calculado automáticamente)
🔴 CRÍTICO (≥1 A4) · 🟠 ALTO (≥2 A3) · 🟡 MEDIO (≥1 A3 o ≥3 A2) · 🟢 NORMAL · ⚪ RUTINARIO

---

## Horarios (cron)

Venezuela (VET, UTC-4) no usa horario de verano.

| Turno | VET | UTC | Profile | Calls |
|-------|-----|-----|---------|-------|
| Matutino (briefing) | 11:00 | `0 15 * * *` | matutino | 10 |
| Cierre (informe principal) | 18:00 | `0 22 * * *` | cierre | 15 |

---

## Archivos del repositorio

```
.
├── .github/workflows/centinela_pro.yml   # Workflow de GitHub Actions
├── config/
│   ├── monitor_noticias_multicapa_ve_v1_1.json   # Metodología (10 capas)
│   └── actores.json                              # Catálogo de actores (códigos)
├── buscador.py        # Fase A — búsqueda multinivel
├── redactor.py        # Fases B+D — redacción HTML + novedades
├── entrega.py         # Fase C — distribución multicanal
├── estado_pipeline.py # Fase D — memoria entre cortes
├── monitor.py         # Orquestador
├── requirements.txt
└── README.md
```

---

## Configuración (no-coder)

### 1) Cargar secretos
**Settings → Secrets and variables → Actions → New repository secret**

Obligatorios:
- `PERPLEXITY_API_KEY` — búsqueda
- `MISTRAL_API_KEY` — redacción

Recomendados:
- `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` — entrega Telegram (chat ID numérico)
- `PAGES_BASE_URL` — URL base de GitHub Pages (ej: `https://usuario.github.io/repo`)
  para que el teaser incluya el link al informe completo

Opcionales:
- `DISCORD_WEBHOOK_URL`
- `SLACK_WEBHOOK_URL`
- `GMAIL_REMITENTE`, `GMAIL_APP_PASSWORD`, `GMAIL_DESTINATARIO`

> El workflow usa automáticamente `GITHUB_TOKEN` interno.

### 2) Habilitar GitHub Pages
**Settings → Pages → Build and deployment → Deploy from a branch → `gh-pages` / `(root)`**

La rama `gh-pages` se crea sola en la primera corrida.

### 3) Ramas automáticas
El sistema crea y mantiene solo dos ramas:
- `gh-pages` — informes HTML publicados
- `estado` — snapshots de Fase D (memoria entre cortes, últimos 14)

No tocar manualmente.

### 4) Ejecutar
- **Automático**: según cron (11:00 y 18:00 VET).
- **Manual**: Actions → CENTINELA PRO → Run workflow. Opcionalmente fijar `profile` y `horas_atras`.

---

## Variables de entorno (en el workflow)

| Variable | Default | Función |
|----------|---------|---------|
| `CENTINELA_CONFIG_PATH` | `config/monitor_noticias_multicapa_ve_v1_1.json` | Ruta del JSON metodológico |
| `CENTINELA_ACTORES_PATH` | `config/actores.json` | Ruta del catálogo de actores |
| `CENTINELA_ESTADO` | `1` | Activa Fase D (memoria). `0` la desactiva |
| `CENTINELA_PROFILE` | (auto) | Forzar `matutino` / `cierre` |
| `PERPLEXITY_TIMEOUT` | `45` | Timeout por llamada (s) |
| `ARCHIVE_URLS` | `1` | Activar archivado Wayback/Archive.today |

---

## Costo estimado

~23 calls/día (10 matutino + 15 cierre, descontando el solapamiento de profiles) a
Perplexity Sonar ≈ **$8-10/mes** según el contexto de búsqueda. Mistral añade el costo
de redacción (2 bloques narrativos por corte).

---

## Roadmap

- **Fase D.1** (pendiente): tracking de narrativas estructurales N1-N10 (anticorrupción,
  soberanía, ruptura interna, diálogo EE.UU., Esequibo, etc.) con intensidad ↑↓→.
- **Fase E** (pendiente): módulo de monitoreo temático (deep dive sobre un caso, tipo
  el reporte modelo de extradición), reutilizando la infraestructura existente.
- **Phase 2 diferida**: salida PDF ejecutiva de 2 páginas, integración Google Docs.
