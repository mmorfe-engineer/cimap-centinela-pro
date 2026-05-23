"""
CENTINELA PRO — Redactor (Fase B)
==================================

Reescritura completa del redactor. Cambios clave vs. versión anterior:

1) ESTRUCTURA ALINEADA al output_contract.brief_sections del JSON v1.1
   - 11 secciones canónicas en orden fijo.
   - Cada sección se llena por DATOS (filtrado por capa/categoría/actor),
     no por inferencia libre del modelo.

2) HTML SEMÁNTICO Y RESPONSIVO (no más <pre> que se sale de pantalla)
   - CSS embebido con variables, media queries, breakpoints móviles.
   - Cards con semáforo visual A4-A0 (rojo/naranja/amarillo/verde/gris).
   - Badges de source_type y verification_status.
   - Citas dobles (URL principal + URL secundaria cross-check) + enlaces
     a Wayback / Archive.today cuando estén disponibles.

3) MISTRAL SOLO PARA LOS BLOQUES NARRATIVOS
   - Resumen ejecutivo (5 puntos, empieza con "En esta entrega encontrarás...")
   - Implicaciones estratégicas y temas a monitorear
   - Análisis de contradicciones (cuando hay items contradictorios)
   El resto del documento se renderiza determinísticamente desde data.

4) CATÁLOGO DE ACTORES INTEGRADO
   - Lee actores.json, resuelve códigos (GOB1, P1, INT1) a nombres completos.
   - Aplica iconografía solo cuando el catálogo la define explícitamente.

5) COMPATIBILIDAD PRESERVADA con monitor.py y entrega.py
   - Retorna dict con success, informe_texto, informe_html, metadata, fuentes.
   - La línea "En esta entrega encontrarás" sigue en informe_texto para que
     el teaser de entrega.py la siga extrayendo (será mejorado en Fase C).
"""

from __future__ import annotations

import html
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

# =============================================================================
# Configuración y constantes
# =============================================================================

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
CONFIG_PATH = os.getenv(
    "CENTINELA_CONFIG_PATH",
    "config/monitor_noticias_multicapa_ve_v1_1.json",
)
ACTORES_PATH = os.getenv(
    "CENTINELA_ACTORES_PATH",
    "config/actores.json",
)

# --- Sistema de clasificación A0-A4 ---
ALERT_LEVEL_META: dict[str, dict[str, str]] = {
    "A4": {
        "emoji": "🔴",
        "denom": "Alerta prioritaria",
        "urgencia": "Inmediata (0-3 horas)",
        "bg": "#ffebee",
        "border": "#d32f2f",
        "badge_bg": "#d32f2f",
        "badge_text": "#ffffff",
    },
    "A3": {
        "emoji": "🟠",
        "denom": "Señal estratégica",
        "urgencia": "Antes del próximo corte",
        "bg": "#fff3e0",
        "border": "#f57c00",
        "badge_bg": "#f57c00",
        "badge_text": "#ffffff",
    },
    "A2": {
        "emoji": "🟡",
        "denom": "Narrativa relevante",
        "urgencia": "Seguimiento entre cortes",
        "bg": "#fffde7",
        "border": "#fbc02d",
        "badge_bg": "#fbc02d",
        "badge_text": "#333333",
    },
    "A1": {
        "emoji": "🟢",
        "denom": "Reacción menor",
        "urgencia": "Registro y seguimiento",
        "bg": "#e8f5e9",
        "border": "#388e3c",
        "badge_bg": "#388e3c",
        "badge_text": "#ffffff",
    },
    "A0": {
        "emoji": "⚪",
        "denom": "Actividad rutinaria",
        "urgencia": "Solo archivo",
        "bg": "#f5f5f5",
        "border": "#757575",
        "badge_bg": "#757575",
        "badge_text": "#ffffff",
    },
}

ALERT_LEVEL_RANK = {"A4": 5, "A3": 4, "A2": 3, "A1": 2, "A0": 1}

# --- Sistema SA0-SA4 (señales sociales) ---
SA_LEVEL_META: dict[str, dict[str, str]] = {
    "SA4": {"emoji": "✅", "label": "Confirmado", "default_warning": "Confirmado por fuente primaria."},
    "SA3": {"emoji": "🔶", "label": "Verosímil", "default_warning": "Señal verosímil, aún no confirmada oficialmente."},
    "SA2": {"emoji": "⚠️", "label": "En observación", "default_warning": "Alerta en observación; requiere validación."},
    "SA1": {"emoji": "❔", "label": "No verificada", "default_warning": "Señal no verificada; no tratar como hecho."},
    "SA0": {"emoji": "⛔", "label": "Descartada", "default_warning": "Señal descartada por irrelevante."},
}

# --- Etiquetas de tipo de fuente ---
SOURCE_TYPE_LABELS: dict[str, dict[str, str]] = {
    "official": {"label": "Oficial", "color": "#1565c0"},
    "state_media": {"label": "Medio estatal", "color": "#c62828"},
    "independent_media": {"label": "Medio independiente", "color": "#2e7d32"},
    "opposition_actor": {"label": "Actor opositor", "color": "#6a1b9a"},
    "multilateral": {"label": "Multilateral", "color": "#0277bd"},
    "ngo": {"label": "ONG", "color": "#00838f"},
    "think_tank": {"label": "Think tank", "color": "#4527a0"},
    "academic": {"label": "Académica", "color": "#283593"},
    "market_data": {"label": "Datos de mercado", "color": "#37474f"},
    "social_signal": {"label": "Señal social", "color": "#ad1457"},
    "telegram_signal": {"label": "Telegram", "color": "#0288d1"},
    "osint": {"label": "OSINT", "color": "#558b2f"},
    "opinion": {"label": "Opinión", "color": "#5d4037"},
    "aggregator": {"label": "Agregador", "color": "#616161"},
}

VERIFICATION_LABELS: dict[str, dict[str, str]] = {
    "officially_confirmed": {"label": "Confirmado oficial", "emoji": "✅"},
    "cross_checked": {"label": "Cross-check", "emoji": "✅"},
    "single_source": {"label": "Una fuente", "emoji": "🔸"},
    "unverified": {"label": "No verificado", "emoji": "❓"},
    "contradicted": {"label": "Contradicho", "emoji": "⚠️"},
}

# --- Estado general del día ---
ESTADO_GENERAL_META: dict[str, dict[str, str]] = {
    "CRITICO": {"emoji": "🔴", "label": "CRÍTICO", "color": "#d32f2f"},
    "ALTO": {"emoji": "🟠", "label": "ALTO", "color": "#f57c00"},
    "MEDIO": {"emoji": "🟡", "label": "MEDIO", "color": "#fbc02d"},
    "NORMAL": {"emoji": "🟢", "label": "NORMAL", "color": "#388e3c"},
    "RUTINARIO": {"emoji": "⚪", "label": "RUTINARIO", "color": "#757575"},
}

# --- Mapeo de categorías a secciones del brief ---
CATS_INTERNACIONAL = {"internacional", "geopolitica"}
CATS_ENERGIA = {"energia", "hidrocarburos"}
CATS_SEGURIDAD = {"seguridad"}
CATS_ECONOMIA = {"economia"}
CATS_DDHH = {"ddhh"}

# --- Bloques de actores ---
BLOQUES_GOBIERNO = {"ejecutivo", "legislativo_oficialismo"}
BLOQUES_OPOSICION = {"oposicion"}
BLOQUES_INSTITUCIONAL = {"institucional"}
BLOQUES_INTERNACIONAL = {"internacional"}

# --- Claves de sección del brief (usadas como keys en el dict de agrupación) ---
SECCION_HECHOS_CRITICOS = "hechos_criticos"
SECCION_EJECUTIVO = "ejecutivo"
SECCION_OPOSICION = "oposicion"
SECCION_INTERNACIONAL = "internacional"
SECCION_ENERGIA = "energia"
SECCION_DATOS = "datos_economicos"
SECCION_SEGURIDAD = "seguridad"

# =============================================================================
# Utilidades
# =============================================================================


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(valor: str) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_dt_humano(dt: datetime | None, fallback: str = "") -> str:
    if not dt:
        return fallback
    # VET = UTC-4
    from datetime import timedelta as _td
    vet = dt - _td(hours=4)
    return vet.strftime("%d/%m/%Y %H:%M VET")


def _esc(texto: Any) -> str:
    """Escape HTML."""
    if texto is None:
        return ""
    return html.escape(str(texto), quote=True)


def _safe_get(d: dict | None, key: str, default: Any = "") -> Any:
    if not isinstance(d, dict):
        return default
    v = d.get(key)
    return v if v is not None else default


# =============================================================================
# Carga de configuración y catálogos
# =============================================================================


def _cargar_json_archivo(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cargar_config() -> dict[str, Any]:
    return _cargar_json_archivo(CONFIG_PATH)


def _cargar_actores() -> dict[str, Any]:
    return _cargar_json_archivo(ACTORES_PATH)


def _indexar_actores(actores_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Indexa actores por código."""
    index: dict[str, dict[str, Any]] = {}
    for actor in actores_data.get("actores", []) or []:
        codigo = actor.get("codigo")
        if codigo:
            index[codigo] = actor
    return index


def _indexar_source_registry(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Indexa source_registry por dominio."""
    registry = config.get("source_registry") or []
    index: dict[str, dict[str, Any]] = {}
    for fuente in registry:
        domain = (fuente.get("domain") or "").lower().strip()
        if domain:
            index[domain] = fuente
    return index


def _dominio_de_url(url: str | None) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower().lstrip("www.")
    except Exception:
        return ""


# =============================================================================
# Extracción y procesamiento de hallazgos
# =============================================================================


def _extraer_todos_hallazgos(busqueda: dict[str, Any]) -> list[dict[str, Any]]:
    """Devuelve lista plana de todos los hallazgos de todas las tareas, deduplicada."""
    todos: list[dict[str, Any]] = []
    vistos: set[tuple[str, str]] = set()

    for clave_tarea, salida in (busqueda.get("resultados") or {}).items():
        parsed = (salida or {}).get("parsed") or {}
        for item in parsed.get("hallazgos", []) or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("fuente_url") or "").strip()
            titulo = str(item.get("titulo") or "").strip()
            if not url and not titulo:
                continue
            key = (url, titulo[:80])
            if key in vistos:
                continue
            vistos.add(key)
            item["_tarea_origen"] = clave_tarea
            todos.append(item)
    return todos


def _enriquecer_con_actor(
    hallazgos: list[dict[str, Any]],
    actores_index: dict[str, dict[str, Any]],
) -> None:
    """Mutates each hallazgo to inject actor_info if actor_principal is a known code."""
    for h in hallazgos:
        actor_ref = str(h.get("actor_principal") or "").strip()
        actor_info = actores_index.get(actor_ref)
        if actor_info:
            h["_actor_info"] = actor_info
        else:
            h["_actor_info"] = None


def _enriquecer_con_source_registry(
    hallazgos: list[dict[str, Any]],
    registry_index: dict[str, dict[str, Any]],
) -> None:
    """Tag each hallazgo with source_registry info if domain matches."""
    for h in hallazgos:
        domain = _dominio_de_url(h.get("fuente_url"))
        fuente = registry_index.get(domain)
        if fuente:
            h.setdefault("_source_registry_match", fuente)


def _calcular_estado_general(hallazgos: list[dict[str, Any]]) -> str:
    """Calcula el estado del día según la presencia y cantidad de niveles A4-A0."""
    if not hallazgos:
        return "RUTINARIO"

    niveles = [str(h.get("alert_level") or "A2") for h in hallazgos]
    n_a4 = sum(1 for n in niveles if n == "A4")
    n_a3 = sum(1 for n in niveles if n == "A3")
    n_a2 = sum(1 for n in niveles if n == "A2")

    if n_a4 >= 1:
        return "CRITICO"
    if n_a3 >= 2:
        return "ALTO"
    if n_a3 >= 1 or n_a2 >= 3:
        return "MEDIO"
    return "NORMAL"


def _sort_por_relevancia(hallazgos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordena por alert_level descendente, luego por fecha."""

    def _key(h: dict[str, Any]) -> tuple[int, str]:
        nivel = ALERT_LEVEL_RANK.get(str(h.get("alert_level") or "A0"), 0)
        fecha = str(h.get("fecha_hora_utc") or "")
        return (-nivel, fecha)

    return sorted(hallazgos, key=_key)


def _construir_headlines_para_teaser(
    hallazgos_ordenados: list[dict[str, Any]],
    limite: int = 4,
) -> list[dict[str, Any]]:
    """
    Construye una lista estructurada de titulares top para que entrega.py
    arme el teaser sin tener que parsear el informe_texto.

    Prioriza A4 y A3, completa con A2 si no llega al límite.
    Acorta títulos a ~80 caracteres para que el teaser sea compacto.
    Fase D: incluye flag `es_nuevo` si el hallazgo viene en novedades.hallazgos_nuevos.
    """
    headlines = []
    for h in hallazgos_ordenados:
        if len(headlines) >= limite:
            break
        nivel = str(h.get("alert_level") or "A2")
        if nivel not in ("A4", "A3", "A2"):
            continue
        actor_info = h.get("_actor_info") or {}
        actor_nombre = actor_info.get("nombre") or str(h.get("actor_principal") or "")
        actor_codigo = actor_info.get("codigo") or ""
        iconografia = actor_info.get("iconografia") or ""
        titulo_raw = str(h.get("titulo") or h.get("fact_summary") or "").strip()
        # Compactar: una sola línea, máximo ~80 chars
        titulo_compacto = re.sub(r"\s+", " ", titulo_raw)
        if len(titulo_compacto) > 80:
            titulo_compacto = titulo_compacto[:77].rstrip() + "..."

        headlines.append({
            "rank": len(headlines) + 1,
            "alert_level": nivel,
            "emoji": ALERT_LEVEL_META.get(nivel, {}).get("emoji", ""),
            "actor_nombre": actor_nombre,
            "actor_codigo": actor_codigo,
            "iconografia": iconografia,
            "titulo": titulo_compacto,
            "fuente_url": h.get("fuente_url", ""),
            "es_nuevo": bool(h.get("_es_nuevo")),
        })
    return headlines


def _filtrar_por_capa(hallazgos: list[dict[str, Any]], capa_id: int) -> list[dict[str, Any]]:
    """Filtra hallazgos por número de capa."""
    return [h for h in hallazgos if h.get("capa") == f"capa_{capa_id}"]


def _filtrar_por_categoria(hallazgos: list[dict[str, Any]], cats: set[str]) -> list[dict[str, Any]]:
    cats_norm = {c.lower() for c in cats}
    return [
        h for h in hallazgos
        if str(h.get("categoria") or "").lower() in cats_norm
    ]


def _filtrar_por_bloque_actor(
    hallazgos: list[dict[str, Any]],
    bloques: set[str],
) -> list[dict[str, Any]]:
    """Filtra hallazgos cuyo actor_principal pertenece a alguno de los bloques."""
    resultado = []
    for h in hallazgos:
        actor_info = h.get("_actor_info")
        if not actor_info:
            continue
        if actor_info.get("bloque") in bloques:
            resultado.append(h)
    return resultado


def _filtrar_alertas_sociales(hallazgos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hallazgos con social_alert_level SA1-SA3 (no SA4 ni SA0)."""
    sa_visibles = {"SA1", "SA2", "SA3"}
    return [
        h for h in hallazgos
        if h.get("social_alert_level") in sa_visibles
    ]


def _filtrar_contradicciones(hallazgos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        h for h in hallazgos
        if h.get("verification_status") == "contradicted"
    ]


def _agrupar_por_seccion(
    hallazgos: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """
    Distribuye cada hallazgo a una sección del brief según prioridad:
    1. Si capa 4 → internacional
    2. Si capa 5 → energía
    3. Si capa 7 o categoría economia → datos económicos
    4. Si capa 9 o categoría seguridad → seguridad
    5. Si actor en bloque GOB/legislativo_oficialismo → anuncios ejecutivo
    6. Si actor en bloque oposicion → reacciones oposición
    7. Resto → hechos críticos si A4/A3, sino contexto
    """
    secciones: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for h in hallazgos:
        capa_str = str(h.get("capa") or "")
        categoria = str(h.get("categoria") or "").lower()
        actor_info = h.get("_actor_info") or {}
        bloque = actor_info.get("bloque") or ""
        alert_level = str(h.get("alert_level") or "A2")

        # Prioridad de clasificación
        if capa_str == "capa_4" or categoria in CATS_INTERNACIONAL:
            secciones[SECCION_INTERNACIONAL].append(h)
        elif capa_str == "capa_5" or categoria in CATS_ENERGIA:
            secciones[SECCION_ENERGIA].append(h)
        elif capa_str in ("capa_6", "capa_7") or categoria in CATS_ECONOMIA:
            secciones[SECCION_DATOS].append(h)
        elif capa_str == "capa_9" or categoria in CATS_SEGURIDAD:
            secciones[SECCION_SEGURIDAD].append(h)
        elif bloque in BLOQUES_GOBIERNO:
            secciones[SECCION_EJECUTIVO].append(h)
        elif bloque in BLOQUES_OPOSICION:
            secciones[SECCION_OPOSICION].append(h)
        elif alert_level in ("A4", "A3"):
            secciones[SECCION_HECHOS_CRITICOS].append(h)
        else:
            # Si no clasifica claro pero es nacional, va a hechos críticos
            secciones[SECCION_HECHOS_CRITICOS].append(h)

    # Ordenar cada sección por relevancia
    for k in secciones:
        secciones[k] = _sort_por_relevancia(secciones[k])

    return dict(secciones)


def _actores_en_silencio(
    hallazgos: list[dict[str, Any]],
    actores_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Actores de alta prioridad que NO aparecen en hallazgos de hoy."""
    actores_con_hallazgo: set[str] = set()
    for h in hallazgos:
        actor_ref = str(h.get("actor_principal") or "").strip()
        if actor_ref in actores_index:
            actores_con_hallazgo.add(actor_ref)
    silencios = []
    for codigo, actor in actores_index.items():
        if actor.get("prioridad_monitoreo") == "alta" and codigo not in actores_con_hallazgo:
            silencios.append(actor)
    return silencios


# =============================================================================
# Mistral — bloques narrativos
# =============================================================================


def _call_mistral(prompt: str, max_tokens: int = 800, system_msg: str | None = None) -> str | None:
    """Llamada a Mistral. Retorna None si falla o no hay API key."""
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        return None

    modelo = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
    sys_msg = system_msg or (
        "Redacta español venezolano profesional, conciso, sin floritura. "
        "No inventes datos. Si no hay evidencia, dilo. "
        "Salida directa sin preámbulo del tipo 'Aquí tienes...' o 'Claro:'."
    )

    payload = {
        "model": modelo,
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt},
        ],
    }

    try:
        response = requests.post(
            MISTRAL_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", [])
        if choices and isinstance(choices, list):
            content = (choices[0].get("message") or {}).get("content", "")
            return content.strip() if content else None
    except Exception:
        return None
    return None


def _contexto_para_resumen(hallazgos_top: list[dict[str, Any]]) -> str:
    """Compacta los hallazgos top para usar como contexto en prompts a Mistral."""
    lineas = []
    for h in hallazgos_top[:12]:  # max 12 hallazgos top para el prompt
        nivel = h.get("alert_level", "A2")
        actor_info = h.get("_actor_info") or {}
        actor_nombre = actor_info.get("nombre") or h.get("actor_principal") or "sin actor"
        fact = h.get("fact_summary") or h.get("titulo") or ""
        why = h.get("why_it_matters") or ""
        fuente = h.get("fuente_nombre") or ""
        lineas.append(
            f"[{nivel}] {actor_nombre}: {fact} "
            f"(Por qué importa: {why}) "
            f"Fuente: {fuente}"
        )
    return "\n".join(lineas)


def _generar_resumen_ejecutivo(
    hallazgos_top: list[dict[str, Any]],
    estado: str,
    correlativo: str,
) -> str:
    """Genera 5 puntos del resumen ejecutivo con Mistral, o fallback determinístico."""
    contexto = _contexto_para_resumen(hallazgos_top)
    if not contexto:
        return (
            "En esta entrega encontrarás un panorama sin hallazgos críticos verificables "
            "dentro del rango UTC analizado. El monitor ejecutó sus capas regulares y "
            "no detectó eventos de alto impacto decisional. Se recomienda continuar el "
            "seguimiento ordinario."
        )

    estado_label = ESTADO_GENERAL_META.get(estado, {}).get("label", "NORMAL")
    n_a4 = sum(1 for h in hallazgos_top if h.get("alert_level") == "A4")
    n_a3 = sum(1 for h in hallazgos_top if h.get("alert_level") == "A3")

    prompt = f"""Redacta el RESUMEN EJECUTIVO del informe CENTINELA PRO con corte {correlativo}.

ESTADO GENERAL DEL DÍA: {estado_label} ({n_a4} hallazgos A4, {n_a3} hallazgos A3)

HALLAZGOS PRINCIPALES (ordenados por relevancia):
{contexto}

REGLAS DE REDACCIÓN:
- 5 puntos numerados, cada uno de 1-2 frases.
- TODO el resumen debe empezar con la frase exacta: "En esta entrega encontrarás:"
- Después de esa frase, lista los 5 puntos.
- Cada punto debe mencionar al menos un hecho verificable de los hallazgos arriba.
- No inventes datos. No agregues análisis fuera de los hallazgos.
- Tono periodístico-analítico, conciso.

FORMATO DE SALIDA (sin markdown, texto plano):
En esta entrega encontrarás:
1. [punto 1]
2. [punto 2]
3. [punto 3]
4. [punto 4]
5. [punto 5]
"""
    resultado = _call_mistral(prompt, max_tokens=600)
    if not resultado:
        # Fallback determinístico mínimo
        lineas = ["En esta entrega encontrarás:"]
        for i, h in enumerate(hallazgos_top[:5], 1):
            actor = (h.get("_actor_info") or {}).get("nombre") or h.get("actor_principal") or ""
            fact = h.get("fact_summary") or h.get("titulo") or ""
            lineas.append(f"{i}. {actor}: {fact}")
        return "\n".join(lineas)
    return resultado


def _generar_implicaciones(
    hallazgos_top: list[dict[str, Any]],
    silencios: list[dict[str, Any]],
) -> str:
    """Genera la sección de implicaciones estratégicas con Mistral."""
    if not hallazgos_top:
        return (
            "Sin hallazgos suficientes en el rango analizado para derivar implicaciones "
            "estratégicas. Continuar monitoreo regular."
        )

    contexto = _contexto_para_resumen(hallazgos_top)
    silencios_str = ""
    if silencios:
        nombres_silencio = ", ".join(
            f"{a.get('nombre','')} ({a.get('codigo','')})" for a in silencios[:6]
        )
        silencios_str = f"\nACTORES DE ALTA PRIORIDAD SIN ACTIVIDAD HOY: {nombres_silencio}"

    prompt = f"""A partir de los hallazgos del informe, identifica IMPLICACIONES ESTRATÉGICAS y TEMAS A MONITOREAR en las próximas horas/días.

HALLAZGOS:
{contexto}
{silencios_str}

REGLAS:
- 3-5 ítems numerados.
- Cada ítem lleva al inicio uno de estos emojis de prioridad: 🔴 (alta) / 🟠 (media) / 🟡 (baja).
- Después del emoji, una frase corta sobre QUÉ monitorear (tema, actor, fecha clave).
- Después, una frase explicando POR QUÉ.
- Si hay actores en silencio relevantes, incluye uno como ítem (silencio puede ser dato).
- No inventes. Basate solo en los hallazgos y silencios listados.

FORMATO DE SALIDA (texto plano):
1. 🔴 [Tema] — [Razón]
2. 🟠 [Tema] — [Razón]
...
"""
    resultado = _call_mistral(prompt, max_tokens=600)
    if not resultado:
        # Fallback determinístico
        lineas = []
        for i, h in enumerate(hallazgos_top[:3], 1):
            actor = (h.get("_actor_info") or {}).get("nombre") or h.get("actor_principal") or ""
            emoji = "🔴" if h.get("alert_level") == "A4" else "🟠"
            lineas.append(
                f"{i}. {emoji} Seguimiento de {actor} — {h.get('why_it_matters','evolución del tema')}"
            )
        return "\n".join(lineas) if lineas else "Continuar monitoreo regular."
    return resultado


def _generar_contradicciones(items_contradictorios: list[dict[str, Any]]) -> str:
    """Genera análisis de contradicciones con Mistral (solo si hay candidatos)."""
    if not items_contradictorios:
        return ""

    contexto = "\n".join(
        f"- {h.get('titulo','')}: {h.get('fact_summary','')} "
        f"[fuente: {h.get('fuente_nombre','')}]"
        for h in items_contradictorios[:6]
    )

    prompt = f"""Analiza las siguientes CONTRADICCIONES O DESVIACIONES detectadas en los hallazgos del día:

ITEMS CONTRADICTORIOS:
{contexto}

REGLAS:
- Para cada contradicción, escribe un bloque corto numerado.
- Cada bloque debe identificar: qué dice una fuente, qué dice la otra (si aplica), qué está confirmado, qué queda por confirmar.
- No resuelvas la contradicción por suposición — si no hay evidencia, dilo.
- Máximo 3 bloques.

FORMATO DE SALIDA (texto plano):
DESVIACIÓN 1 — [Título corto]
[3-4 frases de análisis]

DESVIACIÓN 2 — [...]
...
"""
    return _call_mistral(prompt, max_tokens=600) or ""


# =============================================================================
# CSS embebido
# =============================================================================

CSS_INFORME = """
:root {
  --bg-page: #fafafa;
  --bg-card: #ffffff;
  --text-primary: #1a1a1a;
  --text-secondary: #555;
  --text-muted: #888;
  --border: #e0e0e0;
  --link: #1565c0;
  --link-hover: #0d47a1;
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background: var(--bg-page);
  color: var(--text-primary);
  line-height: 1.6;
  font-size: 16px;
  word-wrap: break-word;
  overflow-wrap: break-word;
}

.contenedor {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 20px 80px;
}

@media (max-width: 640px) {
  body { font-size: 15px; }
  .contenedor { padding: 16px 12px 40px; }
}

header.informe-header {
  border-bottom: 3px solid var(--border);
  padding-bottom: 20px;
  margin-bottom: 28px;
}

header.informe-header h1 {
  margin: 0 0 4px;
  font-size: 1.9em;
  font-weight: 700;
  letter-spacing: -0.5px;
}

header.informe-header .subtitulo {
  color: var(--text-secondary);
  font-size: 0.95em;
  margin-bottom: 16px;
}

.estado-general {
  display: inline-block;
  padding: 10px 18px;
  border-radius: 6px;
  font-weight: 700;
  font-size: 1.05em;
  margin-bottom: 14px;
  color: white;
}

.corte-info {
  font-size: 0.88em;
  color: var(--text-secondary);
  line-height: 1.5;
}

.corte-info code {
  background: #eee;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 0.95em;
}

.quick-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.stat-pill {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 4px 12px;
  font-size: 0.85em;
  color: var(--text-secondary);
}

section.brief {
  margin: 32px 0;
}

section.brief h2 {
  font-size: 1.35em;
  font-weight: 700;
  margin: 0 0 14px;
  padding-bottom: 6px;
  border-bottom: 2px solid var(--border);
}

section.brief .seccion-vacia {
  color: var(--text-muted);
  font-style: italic;
  padding: 8px 0;
}

.resumen-content {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 22px;
  white-space: pre-line;
  line-height: 1.7;
}

.hallazgo {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-left-width: 5px;
  border-radius: 6px;
  padding: 14px 18px;
  margin-bottom: 12px;
}

.hallazgo.nivel-A4 { border-left-color: #d32f2f; background: #ffebee; }
.hallazgo.nivel-A3 { border-left-color: #f57c00; background: #fff3e0; }
.hallazgo.nivel-A2 { border-left-color: #fbc02d; background: #fffde7; }
.hallazgo.nivel-A1 { border-left-color: #388e3c; background: #e8f5e9; }
.hallazgo.nivel-A0 { border-left-color: #757575; background: #f5f5f5; }

.hallazgo-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
  align-items: center;
}

.hallazgo h3 {
  margin: 0 0 8px;
  font-size: 1.08em;
  font-weight: 600;
}

.hallazgo p {
  margin: 6px 0;
}

.hallazgo .fact-summary {
  color: var(--text-primary);
}

.hallazgo .why-matters {
  font-size: 0.92em;
  color: var(--text-secondary);
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
}

.hallazgo .why-matters strong { color: var(--text-primary); }

.hallazgo .info-actor {
  font-size: 0.9em;
  color: var(--text-secondary);
}

.hallazgo .info-actor strong { color: var(--text-primary); }

.hallazgo .enlaces {
  margin-top: 10px;
  font-size: 0.88em;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.hallazgo .enlaces a {
  color: var(--link);
  text-decoration: none;
  border-bottom: 1px dotted var(--link);
}

.hallazgo .enlaces a:hover { color: var(--link-hover); }

.badge {
  display: inline-block;
  padding: 3px 9px;
  border-radius: 4px;
  font-size: 0.74em;
  font-weight: 700;
  letter-spacing: 0.3px;
  text-transform: uppercase;
  white-space: nowrap;
}

.badge-A4 { background: #d32f2f; color: white; }
.badge-A3 { background: #f57c00; color: white; }
.badge-A2 { background: #fbc02d; color: #333; }
.badge-A1 { background: #388e3c; color: white; }
.badge-A0 { background: #757575; color: white; }

.badge-source { color: white; }
.badge-verif {
  background: #f0f0f0;
  color: #444;
  text-transform: none;
  font-weight: 500;
}

.warning-social {
  background: #fff8e1;
  border-left: 4px solid #ffa000;
  padding: 10px 14px;
  margin: 10px 0;
  border-radius: 4px;
  font-size: 0.9em;
  color: #6d4c00;
}

.warning-social strong { color: #5d4037; }

.tabla-fuentes {
  width: 100%;
  border-collapse: collapse;
  margin-top: 8px;
  font-size: 0.92em;
}

.tabla-fuentes th {
  text-align: left;
  background: #f0f0f0;
  padding: 8px 12px;
  border-bottom: 2px solid var(--border);
  font-weight: 600;
}

.tabla-fuentes td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}

.tabla-fuentes td a {
  color: var(--link);
  word-break: break-all;
}

.implicaciones-content,
.contradicciones-content {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px 20px;
  white-space: pre-line;
  line-height: 1.7;
}

.silencios-list {
  background: #f9f5e7;
  border: 1px dashed #d4b886;
  border-radius: 6px;
  padding: 12px 16px;
  font-size: 0.88em;
  margin-top: 14px;
  color: #6d5800;
}

.silencios-list strong { color: #4a3a00; }

footer.informe-footer {
  margin-top: 56px;
  padding-top: 20px;
  border-top: 1px solid var(--border);
  font-size: 0.82em;
  color: var(--text-muted);
  text-align: center;
}

footer.informe-footer p { margin: 4px 0; }

/* === Sección de novedades vs corte anterior (Fase D) === */
.novedades-primer-corte {
  background: #e3f2fd;
  border: 1px dashed #1976d2;
  border-radius: 6px;
  padding: 14px 18px;
  color: #0d47a1;
  font-size: 0.95em;
}

.novedades-bloque {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px 16px;
  margin-bottom: 10px;
  font-size: 0.95em;
}

.novedades-bloque.cambio-escalada {
  background: #fff3e0;
  border-left: 4px solid #f57c00;
}

.novedades-bloque.cambio-desescalada {
  background: #e8f5e9;
  border-left: 4px solid #388e3c;
}

.novedades-bloque.cambio-igual {
  background: #f5f5f5;
  border-left: 4px solid #757575;
}

.cambio-etiqueta {
  margin-left: 8px;
  font-size: 0.8em;
  background: #424242;
  color: white;
  padding: 2px 8px;
  border-radius: 10px;
  letter-spacing: 0.5px;
}

.novedades-bloque.novedades-nuevos {
  background: #e8f5e9;
  border-left: 4px solid #2e7d32;
}

.novedades-bloque.novedades-persistentes {
  background: #fff8e1;
  border-left: 4px solid #f9a825;
}

.novedades-bloque.novedades-actores {
  border-left: 4px solid #1565c0;
}

.novedades-bloque.novedades-silencios {
  background: #fafafa;
  border-left: 4px solid #6a1b9a;
}

.novedades-bloque.novedades-a4 {
  background: #ffebee;
  border-left: 4px solid #c62828;
}

.novedades-lista {
  margin: 8px 0 0;
  padding-left: 24px;
}

.novedades-lista li {
  margin-bottom: 6px;
  line-height: 1.5;
}

.novedades-lista code {
  background: rgba(0,0,0,0.06);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.92em;
}
"""

# =============================================================================
# Renderizado HTML
# =============================================================================


def _render_badge_nivel(nivel: str) -> str:
    meta = ALERT_LEVEL_META.get(nivel, ALERT_LEVEL_META["A2"])
    return f'<span class="badge badge-{nivel}">{meta["emoji"]} {nivel}</span>'


def _render_badge_source(source_type: str) -> str:
    meta = SOURCE_TYPE_LABELS.get(source_type, {"label": source_type or "n/d", "color": "#888"})
    color = meta.get("color", "#888")
    label = _esc(meta.get("label", source_type))
    return f'<span class="badge badge-source" style="background:{color};">{label}</span>'


def _render_badge_verif(estado: str) -> str:
    meta = VERIFICATION_LABELS.get(estado, {"label": estado or "?", "emoji": ""})
    emoji = meta.get("emoji", "")
    label = _esc(meta.get("label", estado))
    return f'<span class="badge badge-verif">{emoji} {label}</span>'


def _render_info_actor(hallazgo: dict[str, Any]) -> str:
    actor_info = hallazgo.get("_actor_info")
    if actor_info:
        nombre = _esc(actor_info.get("nombre", ""))
        codigo = _esc(actor_info.get("codigo", ""))
        cargo = _esc(actor_info.get("cargo", ""))
        icono = actor_info.get("iconografia") or ""
        # icono solo si el catálogo lo define
        icono_str = f" {icono}" if icono else ""
        return (
            f'<p class="info-actor">'
            f'<strong>{codigo}</strong> — {nombre}{icono_str}'
            + (f' <em>({cargo})</em>' if cargo else "")
            + '</p>'
        )
    # Actor no catalogado
    actor_raw = _esc(hallazgo.get("actor_principal") or "")
    if actor_raw:
        return f'<p class="info-actor">Actor: {actor_raw}</p>'
    return ""


def _render_enlaces_hallazgo(hallazgo: dict[str, Any]) -> str:
    enlaces = []
    fuente_nombre = _esc(hallazgo.get("fuente_nombre") or "Fuente")
    url = hallazgo.get("fuente_url")
    if url:
        enlaces.append(f'<a href="{_esc(url)}" target="_blank" rel="noopener">📰 {fuente_nombre}</a>')
    url2 = hallazgo.get("fuente_url_secundaria")
    if url2:
        enlaces.append(f'<a href="{_esc(url2)}" target="_blank" rel="noopener">🔁 Cross-check</a>')
    archivos = hallazgo.get("archivos") or {}
    wayback = archivos.get("wayback")
    if wayback:
        enlaces.append(f'<a href="{_esc(wayback)}" target="_blank" rel="noopener">🗄️ Wayback</a>')
    archive_today = archivos.get("archive_today")
    if archive_today:
        enlaces.append(f'<a href="{_esc(archive_today)}" target="_blank" rel="noopener">🗄️ Archive.today</a>')
    if not enlaces:
        return ""
    return '<div class="enlaces">' + " · ".join(enlaces) + "</div>"


def _render_warning_social(hallazgo: dict[str, Any]) -> str:
    sa_level = hallazgo.get("social_alert_level")
    if not sa_level or sa_level == "SA4":
        return ""
    meta = SA_LEVEL_META.get(sa_level, {})
    warning = hallazgo.get("warning_label") or meta.get("default_warning", "")
    emoji = meta.get("emoji", "⚠️")
    label = _esc(meta.get("label", sa_level))
    return (
        f'<div class="warning-social">'
        f'<strong>{emoji} {label}:</strong> {_esc(warning)}'
        f'</div>'
    )


def _render_hallazgo_card(hallazgo: dict[str, Any]) -> str:
    nivel = str(hallazgo.get("alert_level") or "A2")
    if nivel not in ALERT_LEVEL_META:
        nivel = "A2"

    source_type = str(hallazgo.get("source_type") or "independent_media")
    verif = str(hallazgo.get("verification_status") or "single_source")
    titulo = _esc(hallazgo.get("titulo") or "Sin título")
    fact_summary = _esc(hallazgo.get("fact_summary") or "")
    why_matters = _esc(hallazgo.get("why_it_matters") or "")
    fecha_dt = _parse_dt(str(hallazgo.get("fecha_hora_utc") or ""))
    fecha_str = _fmt_dt_humano(fecha_dt) if fecha_dt else _esc(hallazgo.get("fecha_hora_utc") or "")

    # Data points
    data_points = hallazgo.get("data_points") or []
    dp_html = ""
    if isinstance(data_points, list) and data_points:
        dp_items = "".join(f"<li>{_esc(str(d))}</li>" for d in data_points[:6])
        dp_html = f'<ul class="data-points">{dp_items}</ul>'

    badges_html = " ".join([
        _render_badge_nivel(nivel),
        _render_badge_source(source_type),
        _render_badge_verif(verif),
    ])

    info_actor_html = _render_info_actor(hallazgo)
    enlaces_html = _render_enlaces_hallazgo(hallazgo)
    warning_html = _render_warning_social(hallazgo)

    fecha_html = (
        f'<p class="info-actor"><em>Publicado:</em> {_esc(fecha_str)}</p>'
        if fecha_str else ""
    )

    why_html = ""
    if why_matters:
        why_html = (
            f'<div class="why-matters">'
            f'<strong>Por qué importa:</strong> {why_matters}'
            f'</div>'
        )

    return f"""
<div class="hallazgo nivel-{nivel}">
  <div class="hallazgo-meta">{badges_html}</div>
  <h3>{titulo}</h3>
  <p class="fact-summary">{fact_summary}</p>
  {info_actor_html}
  {fecha_html}
  {dp_html}
  {warning_html}
  {why_html}
  {enlaces_html}
</div>
""".strip()


def _render_seccion(
    numero: int,
    titulo: str,
    hallazgos: list[dict[str, Any]],
    mensaje_vacio: str = "Sin hallazgos en esta sección durante el rango analizado.",
) -> str:
    """Renderiza una sección con hallazgos como cards."""
    if not hallazgos:
        cuerpo = f'<p class="seccion-vacia">{_esc(mensaje_vacio)}</p>'
    else:
        cuerpo = "\n".join(_render_hallazgo_card(h) for h in hallazgos)
    return f"""
<section class="brief">
  <h2>{numero}. {_esc(titulo)}</h2>
  {cuerpo}
</section>
""".strip()


def _render_seccion_prosa(numero: int, titulo: str, prosa: str, css_class: str = "implicaciones-content") -> str:
    """Renderiza una sección que contiene texto narrativo generado por Mistral."""
    if not prosa or not prosa.strip():
        return f'<section class="brief"><h2>{numero}. {_esc(titulo)}</h2><p class="seccion-vacia">Sin contenido disponible.</p></section>'
    # Escape pero respetar saltos de línea (white-space: pre-line en CSS hace el resto)
    return f"""
<section class="brief">
  <h2>{numero}. {_esc(titulo)}</h2>
  <div class="{css_class}">{_esc(prosa)}</div>
</section>
""".strip()


def _render_seccion_novedades(
    numero: int,
    novedades: dict[str, Any] | None,
    actores_index: dict[str, dict[str, Any]],
) -> str:
    """
    Renderiza la sección "Novedades respecto al corte anterior" (Fase D).
    Muestra:
    - Cambio de estado general (escalada/desescalada)
    - Hallazgos nuevos vs persistentes
    - Actores que aparecieron / callaron
    - Silencios prolongados
    - Alertas A4 nuevas / persistentes / resueltas
    """
    if not novedades:
        return ""

    if novedades.get("es_primer_corte"):
        msg = novedades.get("mensaje", "Primer corte registrado en el sistema.")
        return f"""
<section class="brief">
  <h2>{numero}. Novedades respecto al corte anterior</h2>
  <div class="novedades-primer-corte">
    📍 {_esc(msg)}
  </div>
</section>
""".strip()

    ref = novedades.get("ref_corte_anterior") or {}
    correlativo_prev = ref.get("correlativo", "")
    turno_prev = ref.get("turno", "")

    bloques = []

    # --- Cambio de estado ---
    cambio = novedades.get("cambio_estado") or {}
    if cambio:
        previo = cambio.get("previo", "NORMAL")
        actual = cambio.get("actual", "NORMAL")
        direccion = cambio.get("direccion", "igual")
        if direccion == "escalada":
            flecha = "⬆️"
            color_class = "cambio-escalada"
            etiqueta = "ESCALADA"
        elif direccion == "desescalada":
            flecha = "⬇️"
            color_class = "cambio-desescalada"
            etiqueta = "DESESCALADA"
        else:
            flecha = "→"
            color_class = "cambio-igual"
            etiqueta = "SIN CAMBIO"
        meta_prev = ESTADO_GENERAL_META.get(previo, ESTADO_GENERAL_META["NORMAL"])
        meta_act = ESTADO_GENERAL_META.get(actual, ESTADO_GENERAL_META["NORMAL"])
        bloques.append(f"""
<div class="novedades-bloque {color_class}">
  <strong>{flecha} CAMBIO DE ESTADO:</strong>
  <span style="color:{meta_prev['color']}; font-weight:600;">{meta_prev['emoji']} {meta_prev['label']}</span>
  &rarr;
  <span style="color:{meta_act['color']}; font-weight:600;">{meta_act['emoji']} {meta_act['label']}</span>
  <span class="cambio-etiqueta">[{etiqueta}]</span>
</div>
""")

    # --- Hallazgos nuevos ---
    nuevos = novedades.get("hallazgos_nuevos") or []
    if nuevos:
        items_html = []
        nuevos_ordenados = sorted(
            nuevos,
            key=lambda x: -ALERT_LEVEL_RANK.get(str(x.get("alert_level") or "A0"), 0),
        )
        for n in nuevos_ordenados[:10]:
            nivel = str(n.get("alert_level") or "A2")
            emoji = ALERT_LEVEL_META.get(nivel, {}).get("emoji", "")
            actor_ref = str(n.get("actor") or "")
            actor_info = actores_index.get(actor_ref, {})
            actor_label = actor_info.get("nombre", actor_ref) if actor_info else actor_ref
            titulo = _esc(n.get("titulo") or "")
            url = n.get("fuente_url") or ""
            link = f' <a href="{_esc(url)}" target="_blank" rel="noopener" style="font-size:0.85em;">↗</a>' if url else ""
            items_html.append(
                f'<li><span class="badge badge-{nivel}">{emoji} {nivel}</span> '
                f'<strong>{_esc(actor_label)}</strong>: {titulo}{link}</li>'
            )
        bloques.append(f"""
<div class="novedades-bloque novedades-nuevos">
  <strong>🆕 HALLAZGOS NUEVOS DESDE EL CORTE ANTERIOR ({len(nuevos)}):</strong>
  <ul class="novedades-lista">{''.join(items_html)}</ul>
</div>
""")

    # --- Hallazgos persistentes (tema sigue activo) ---
    persistentes = novedades.get("hallazgos_persistentes") or []
    if persistentes:
        items_html = []
        for p in persistentes[:5]:
            nivel = str(p.get("alert_level") or "A2")
            actor_ref = str(p.get("actor") or "")
            actor_info = actores_index.get(actor_ref, {})
            actor_label = actor_info.get("nombre", actor_ref) if actor_info else actor_ref
            titulo = _esc(p.get("titulo") or "")
            items_html.append(
                f'<li><span class="badge badge-{nivel}">{nivel}</span> '
                f'<strong>{_esc(actor_label)}</strong>: {titulo}</li>'
            )
        bloques.append(f"""
<div class="novedades-bloque novedades-persistentes">
  <strong>🔁 TEMAS QUE PERSISTEN ENTRE CORTES ({len(persistentes)}):</strong>
  <ul class="novedades-lista">{''.join(items_html)}</ul>
</div>
""")

    # --- Actores que aparecieron / callaron ---
    aparecieron = novedades.get("actores_que_aparecieron") or []
    callaron = novedades.get("actores_que_callaron") or []
    if aparecieron or callaron:
        bloque_actores = '<div class="novedades-bloque novedades-actores">'
        if aparecieron:
            nombres = []
            for codigo in aparecieron[:8]:
                info = actores_index.get(codigo, {})
                nombre = info.get("nombre", codigo)
                nombres.append(f"<code>{_esc(codigo)}</code> ({_esc(nombre)})")
            bloque_actores += f'<div><strong>📣 Aparecen hoy:</strong> {", ".join(nombres)}</div>'
        if callaron:
            nombres = []
            for codigo in callaron[:8]:
                info = actores_index.get(codigo, {})
                nombre = info.get("nombre", codigo)
                nombres.append(f"<code>{_esc(codigo)}</code> ({_esc(nombre)})")
            bloque_actores += f'<div style="margin-top:6px;"><strong>🤐 Hablaron en el corte anterior, hoy no:</strong> {", ".join(nombres)}</div>'
        bloque_actores += "</div>"
        bloques.append(bloque_actores)

    # --- Silencios prolongados ---
    silencios_prolong = novedades.get("silencios_prolongados") or []
    if silencios_prolong:
        items_html = []
        for s in silencios_prolong[:6]:
            items_html.append(
                f'<li><code>{_esc(s.get("codigo",""))}</code> — {_esc(s.get("nombre",""))} '
                f'(<em>{_esc(s.get("cargo",""))}</em>): '
                f'<strong>{s.get("cortes_sin_actividad",0)} cortes sin actividad</strong></li>'
            )
        bloques.append(f"""
<div class="novedades-bloque novedades-silencios">
  <strong>📵 SILENCIOS PROLONGADOS (alta prioridad sin actividad ≥2 cortes):</strong>
  <ul class="novedades-lista">{''.join(items_html)}</ul>
</div>
""")

    # --- Alertas A4 ---
    a4_nuevas = novedades.get("alertas_a4_nuevas") or []
    a4_persist = novedades.get("alertas_a4_persistentes") or []
    a4_resueltas = novedades.get("alertas_a4_resueltas") or []
    if a4_nuevas or a4_persist or a4_resueltas:
        bloque_a4 = '<div class="novedades-bloque novedades-a4">'
        bloque_a4 += '<strong>🔴 TRACKING DE ALERTAS A4 (críticas):</strong>'
        if a4_nuevas:
            bloque_a4 += f'<div style="margin-top:6px;"><strong>Nuevas A4:</strong> {len(a4_nuevas)}</div>'
        if a4_persist:
            bloque_a4 += f'<div><strong>A4 que persisten desde corte anterior:</strong> {len(a4_persist)}</div>'
        if a4_resueltas:
            bloque_a4 += f'<div><strong>A4 del corte anterior que ya no aparecen (¿resueltas o desplazadas?):</strong> {len(a4_resueltas)}</div>'
        bloque_a4 += "</div>"
        bloques.append(bloque_a4)

    cuerpo = "\n".join(bloques) if bloques else '<p class="seccion-vacia">Sin novedades estructurales detectadas frente al corte anterior.</p>'

    ref_label = ""
    if correlativo_prev:
        ref_label = f' <span style="font-size:0.85em; color:#888;">(vs. <code>{_esc(correlativo_prev)}</code>)</span>'

    return f"""
<section class="brief">
  <h2>{numero}. Novedades respecto al corte anterior{ref_label}</h2>
  {cuerpo}
</section>
""".strip()


def _render_alertas_tempranas(hallazgos_sociales: list[dict[str, Any]]) -> str:
    if not hallazgos_sociales:
        return _render_seccion(9, "Alertas tempranas con advertencia", [], "No se identificaron señales sociales SA1-SA3 en este corte.")
    return _render_seccion(9, "Alertas tempranas con advertencia", hallazgos_sociales)


def _render_silencios(silencios: list[dict[str, Any]]) -> str:
    """Bloque pequeño con actores en silencio (opcional, pie de implicaciones)."""
    if not silencios:
        return ""
    items = []
    for a in silencios[:8]:
        nombre = _esc(a.get("nombre", ""))
        codigo = _esc(a.get("codigo", ""))
        cargo = _esc(a.get("cargo", ""))
        items.append(f"<li><strong>{codigo}</strong> — {nombre} ({cargo})</li>")
    return f"""
<div class="silencios-list">
  <strong>📵 Actores de alta prioridad sin actividad detectada en este rango:</strong>
  <ul>{"".join(items)}</ul>
  <em>Nota: el silencio puede ser dato. La sección automatizada de "Novedades vs. corte anterior" llegará en Fase D.</em>
</div>
"""


def _agrupar_fuentes_por_tipo(
    hallazgos: list[dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    """Agrupa hallazgos por source_type para la sección de fuentes consultadas."""
    grupos: dict[str, list[dict[str, str]]] = defaultdict(list)
    vistos: set[str] = set()
    for h in hallazgos:
        url = h.get("fuente_url")
        if not url or url in vistos:
            continue
        vistos.add(url)
        st = str(h.get("source_type") or "independent_media")
        grupos[st].append({
            "nombre": h.get("fuente_nombre") or _dominio_de_url(url),
            "url": url,
            "fecha": h.get("fecha_hora_utc") or "",
        })
    return dict(grupos)


def _render_fuentes_consultadas(
    hallazgos: list[dict[str, Any]],
    numero: int,
) -> str:
    grupos = _agrupar_fuentes_por_tipo(hallazgos)
    if not grupos:
        return _render_seccion(numero, "Fuentes consultadas", [], "Sin fuentes registradas.")

    bloques = []
    for tipo, items in sorted(grupos.items(), key=lambda x: -len(x[1])):
        label = SOURCE_TYPE_LABELS.get(tipo, {}).get("label", tipo)
        filas = []
        for it in items[:20]:
            nombre = _esc(it["nombre"])
            url = _esc(it["url"])
            fecha = _esc(it["fecha"][:10] if it["fecha"] else "")
            filas.append(
                f'<tr><td>{nombre}</td><td><a href="{url}" target="_blank" rel="noopener">{url}</a></td><td>{fecha}</td></tr>'
            )
        bloques.append(f"""
<h3 style="font-size:1.05em; margin-top:18px;">{_render_badge_source(tipo)} {_esc(label)} <span style="color:#888; font-weight:400;">({len(items)})</span></h3>
<table class="tabla-fuentes">
  <thead><tr><th>Fuente</th><th>URL</th><th>Fecha</th></tr></thead>
  <tbody>{"".join(filas)}</tbody>
</table>
""")
    cuerpo = "\n".join(bloques)
    return f"""
<section class="brief">
  <h2>{numero}. Fuentes consultadas</h2>
  {cuerpo}
</section>
""".strip()


def _render_header(
    estado: str,
    correlativo: str,
    turno: str,
    rango_inicio: str,
    rango_fin: str,
    n_a4: int,
    n_a3: int,
    n_a2: int,
    n_total: int,
) -> str:
    meta_estado = ESTADO_GENERAL_META.get(estado, ESTADO_GENERAL_META["NORMAL"])
    fecha_gen = _utc_now().strftime("%Y-%m-%d %H:%M UTC")
    turno_label = "Apertura matutina" if turno == "matutino" else "Cierre del día"

    return f"""
<header class="informe-header">
  <h1>CENTINELA PRO</h1>
  <div class="subtitulo">Informe de monitoreo político — Venezuela</div>
  <div class="estado-general" style="background:{meta_estado['color']};">
    {meta_estado['emoji']} ESTADO DEL DÍA: {meta_estado['label']}
  </div>
  <div class="corte-info">
    <strong>Corte:</strong> {_esc(turno_label)} ({_esc(turno)})<br>
    <strong>Correlativo:</strong> <code>{_esc(correlativo)}</code><br>
    <strong>Ventana UTC:</strong> {_esc(rango_inicio)} → {_esc(rango_fin)}<br>
    <strong>Generado:</strong> {_esc(fecha_gen)}
  </div>
  <div class="quick-stats">
    <span class="stat-pill">🔴 {n_a4} A4</span>
    <span class="stat-pill">🟠 {n_a3} A3</span>
    <span class="stat-pill">🟡 {n_a2} A2</span>
    <span class="stat-pill">📊 {n_total} hallazgos totales</span>
  </div>
</header>
"""


def _render_footer() -> str:
    return f"""
<footer class="informe-footer">
  <p>CENTINELA PRO · Fase B (redactor v2.0) · {_utc_now().strftime("%Y-%m-%d")}</p>
  <p>Informe generado automáticamente. Uso estratégico interno; no para distribución sin autorización.</p>
</footer>
"""


def _render_html_completo(
    estado: str,
    correlativo: str,
    turno: str,
    rango_inicio: str,
    rango_fin: str,
    secciones_renderizadas: list[str],
    contadores: dict[str, int],
) -> str:
    header_html = _render_header(
        estado=estado,
        correlativo=correlativo,
        turno=turno,
        rango_inicio=rango_inicio,
        rango_fin=rango_fin,
        n_a4=contadores.get("A4", 0),
        n_a3=contadores.get("A3", 0),
        n_a2=contadores.get("A2", 0),
        n_total=contadores.get("total", 0),
    )

    cuerpo = "\n".join(secciones_renderizadas)
    footer_html = _render_footer()

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CENTINELA PRO · {_esc(correlativo)}</title>
<style>{CSS_INFORME}</style>
</head>
<body>
<div class="contenedor">
{header_html}
{cuerpo}
{footer_html}
</div>
</body>
</html>
"""


# =============================================================================
# Renderizado de texto plano (para Telegram/Discord/Slack)
# =============================================================================


def _render_texto_plano(
    estado: str,
    correlativo: str,
    turno: str,
    rango_inicio: str,
    rango_fin: str,
    resumen_ejecutivo: str,
    hallazgos_top: list[dict[str, Any]],
    implicaciones: str,
    contadores: dict[str, int],
) -> str:
    meta_estado = ESTADO_GENERAL_META.get(estado, ESTADO_GENERAL_META["NORMAL"])
    turno_label = "Apertura matutina" if turno == "matutino" else "Cierre del día"

    lineas = [
        f"CENTINELA PRO — Informe de monitoreo político de Venezuela",
        f"{meta_estado['emoji']} ESTADO DEL DÍA: {meta_estado['label']}",
        "",
        f"Corte: {turno_label} | Correlativo: {correlativo}",
        f"Ventana UTC: {rango_inicio} → {rango_fin}",
        f"Hallazgos: 🔴{contadores.get('A4',0)} A4 · 🟠{contadores.get('A3',0)} A3 · 🟡{contadores.get('A2',0)} A2 · Total {contadores.get('total',0)}",
        "",
        "=" * 68,
        "RESUMEN EJECUTIVO",
        "=" * 68,
        resumen_ejecutivo,
        "",
    ]

    if hallazgos_top:
        lineas.append("=" * 68)
        lineas.append("HALLAZGOS PRIORITARIOS (A4-A3)")
        lineas.append("=" * 68)
        for i, h in enumerate(hallazgos_top[:8], 1):
            nivel = h.get("alert_level", "A2")
            emoji = ALERT_LEVEL_META.get(nivel, {}).get("emoji", "")
            actor_info = h.get("_actor_info") or {}
            actor = actor_info.get("nombre") or h.get("actor_principal") or ""
            icono = actor_info.get("iconografia") or ""
            icono_str = f" {icono}" if icono else ""
            titulo = h.get("titulo", "")
            fact = h.get("fact_summary", "")
            why = h.get("why_it_matters", "")
            fuente = h.get("fuente_nombre", "")
            url = h.get("fuente_url", "")
            lineas.extend([
                f"{i}. {emoji} {nivel} · {actor}{icono_str}",
                f"   {titulo}",
                f"   {fact}",
                f"   ¿Por qué importa? {why}" if why else "",
                f"   Fuente: {fuente} | {url}",
                "",
            ])

    if implicaciones:
        lineas.append("=" * 68)
        lineas.append("IMPLICACIONES ESTRATÉGICAS Y TEMAS A MONITOREAR")
        lineas.append("=" * 68)
        lineas.append(implicaciones)
        lineas.append("")

    lineas.append("=" * 68)
    lineas.append(f"Fin del informe · {_utc_now().strftime('%Y-%m-%d %H:%M UTC')}")
    lineas.append("CENTINELA PRO · Uso estratégico interno.")

    return "\n".join(lineas)


# =============================================================================
# Función principal
# =============================================================================


def redactar_informe(
    resultado_busqueda: dict[str, Any],
    novedades: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Toma el resultado del buscador y produce informe_texto + informe_html.
    Compatible con monitor.py y entrega.py.

    Fase D: acepta parámetro `novedades` (dict producido por estado_pipeline)
    para renderizar la sección "Novedades vs. corte anterior".
    """
    correlativo = resultado_busqueda.get("correlativo", "sin-correlativo")
    turno = resultado_busqueda.get("turno", "cierre")
    rango_inicio_iso = resultado_busqueda.get("rango_inicio", "")
    rango_fin_iso = resultado_busqueda.get("rango_fin", "")

    config = _cargar_config()
    actores_data = _cargar_actores()
    actores_index = _indexar_actores(actores_data)
    registry_index = _indexar_source_registry(config)

    # 1. Extraer todos los hallazgos planos y deduplicados
    hallazgos = _extraer_todos_hallazgos(resultado_busqueda)
    _enriquecer_con_actor(hallazgos, actores_index)
    _enriquecer_con_source_registry(hallazgos, registry_index)

    # 1b. Construir set de hashes "nuevos" desde novedades (para flag NUEVO en headlines)
    hashes_nuevos_set: set[str] = set()
    if novedades and not novedades.get("es_primer_corte"):
        for n in novedades.get("hallazgos_nuevos") or []:
            if isinstance(n, dict) and n.get("hash"):
                hashes_nuevos_set.add(n["hash"])
    # Inyectar flag _es_nuevo en cada hallazgo (recalculando el hash con misma función)
    if hashes_nuevos_set:
        # Importación local para evitar dependencia dura en tests offline
        try:
            from estado_pipeline import _hash_hallazgo  # type: ignore
            for h in hallazgos:
                actor_ref = str(h.get("actor_principal") or "")
                titulo = str(h.get("titulo") or "")
                h_hash = _hash_hallazgo(actor_ref, titulo)
                h["_es_nuevo"] = h_hash in hashes_nuevos_set
        except ImportError:
            pass

    # 2. Estado general del día
    estado = _calcular_estado_general(hallazgos)
    contadores = {
        "A4": sum(1 for h in hallazgos if h.get("alert_level") == "A4"),
        "A3": sum(1 for h in hallazgos if h.get("alert_level") == "A3"),
        "A2": sum(1 for h in hallazgos if h.get("alert_level") == "A2"),
        "A1": sum(1 for h in hallazgos if h.get("alert_level") == "A1"),
        "A0": sum(1 for h in hallazgos if h.get("alert_level") == "A0"),
        "total": len(hallazgos),
    }

    # 3. Hallazgos top (A4 y A3) para resumen/implicaciones
    hallazgos_ordenados = _sort_por_relevancia(hallazgos)
    hallazgos_top = [h for h in hallazgos_ordenados if h.get("alert_level") in ("A4", "A3")]
    if not hallazgos_top:
        # Si no hay A4/A3, usar los top 8 por relevancia para tener contenido
        hallazgos_top = hallazgos_ordenados[:8]

    # 4. Agrupación por sección del brief
    por_seccion = _agrupar_por_seccion(hallazgos_ordenados)

    # 5. Hallazgos críticos (A4 priorizados, sin importar sección)
    hechos_criticos = [h for h in hallazgos_ordenados if h.get("alert_level") == "A4"]

    # 6. Alertas tempranas (sociales SA1-SA3)
    alertas_tempranas = _filtrar_alertas_sociales(hallazgos_ordenados)

    # 7. Contradicciones
    contradicciones = _filtrar_contradicciones(hallazgos_ordenados)

    # 8. Actores en silencio
    silencios = _actores_en_silencio(hallazgos, actores_index)

    # 9. Generar bloques narrativos con Mistral
    resumen_ejecutivo = _generar_resumen_ejecutivo(hallazgos_top, estado, correlativo)
    implicaciones = _generar_implicaciones(hallazgos_top, silencios)
    analisis_contradicciones = _generar_contradicciones(contradicciones) if contradicciones else ""

    # 10. Construir HTML por secciones — numeración dinámica con contador
    secciones_html: list[str] = []
    _contador = [0]

    def _next() -> int:
        _contador[0] += 1
        return _contador[0]

    # Sección 1: Resumen ejecutivo
    secciones_html.append(
        _render_seccion_prosa(_next(), "Resumen ejecutivo", resumen_ejecutivo, css_class="resumen-content")
    )

    # Sección 2 (Fase D): Novedades vs. corte anterior (solo si tenemos novedades)
    if novedades:
        secciones_html.append(_render_seccion_novedades(_next(), novedades, actores_index))

    # Sección 3: Hechos críticos (solo A4)
    secciones_html.append(
        _render_seccion(_next(), "Hechos críticos (A4 — últimas 24h)", hechos_criticos,
                       "Sin hallazgos A4 en este corte. El día se mantiene sin alertas críticas.")
    )

    # Sección 4: Anuncios y señales del Ejecutivo
    secciones_html.append(
        _render_seccion(_next(), "Anuncios y señales del Ejecutivo",
                       por_seccion.get(SECCION_EJECUTIVO, []),
                       "Sin anuncios verificados del bloque ejecutivo en este corte.")
    )

    # Sección 5: Reacciones políticas internas y oposición
    secciones_html.append(
        _render_seccion(_next(), "Reacciones políticas internas y oposición",
                       por_seccion.get(SECCION_OPOSICION, []),
                       "Sin pronunciamientos verificados del bloque opositor en este corte.")
    )

    # Sección 6: Lectura internacional por focos geográficos
    secciones_html.append(
        _render_seccion(_next(), "Lectura internacional por focos geográficos",
                       por_seccion.get(SECCION_INTERNACIONAL, []),
                       "Sin cobertura internacional relevante en este corte.")
    )

    # Sección 7: Energía / petróleo / sanciones / mercados
    secciones_html.append(
        _render_seccion(_next(), "Energía, petróleo, sanciones y mercados",
                       por_seccion.get(SECCION_ENERGIA, []),
                       "Sin movimientos energéticos o de sanciones detectados.")
    )

    # Sección 8: Datos económicos y sociales
    secciones_html.append(
        _render_seccion(_next(), "Datos económicos y sociales",
                       por_seccion.get(SECCION_DATOS, []),
                       "Sin actualizaciones de indicadores en este corte.")
    )

    # Sección 9: Seguridad, frontera y riesgos regionales
    secciones_html.append(
        _render_seccion(_next(), "Seguridad, frontera y riesgos regionales",
                       por_seccion.get(SECCION_SEGURIDAD, []),
                       "Sin incidentes de seguridad relevantes detectados.")
    )

    # Sección 10: Alertas tempranas con advertencia (SA1-SA3)
    secciones_html.append(_render_alertas_tempranas(alertas_tempranas) if False
                          else _render_seccion(_next(), "Alertas tempranas con advertencia", alertas_tempranas,
                                                "No se identificaron señales sociales SA1-SA3 en este corte."))

    # Sección 11: Implicaciones estratégicas y temas a monitorear
    impl_num = _next()
    impl_html = _render_seccion_prosa(impl_num, "Implicaciones estratégicas y temas a monitorear", implicaciones)
    if silencios:
        impl_html = impl_html.replace("</section>", _render_silencios(silencios) + "</section>")
    secciones_html.append(impl_html)

    # Sección 12 (extra): Contradicciones y desviaciones (solo si hay)
    if analisis_contradicciones or contradicciones:
        contrad_num = _next()
        contrad_html_inner = _render_seccion_prosa(contrad_num, "Contradicciones y desviaciones",
                                                    analisis_contradicciones or "Items contradictorios detectados; revisar fuentes.",
                                                    css_class="contradicciones-content")
        secciones_html.append(contrad_html_inner)

    # Sección final: Fuentes consultadas
    secciones_html.append(_render_fuentes_consultadas(hallazgos, _next()))

    # 11. HTML completo
    html_completo = _render_html_completo(
        estado=estado,
        correlativo=correlativo,
        turno=turno,
        rango_inicio=rango_inicio_iso,
        rango_fin=rango_fin_iso,
        secciones_renderizadas=secciones_html,
        contadores=contadores,
    )

    # 12. Texto plano para canales sin HTML
    texto_plano = _render_texto_plano(
        estado=estado,
        correlativo=correlativo,
        turno=turno,
        rango_inicio=rango_inicio_iso,
        rango_fin=rango_fin_iso,
        resumen_ejecutivo=resumen_ejecutivo,
        hallazgos_top=hallazgos_top,
        implicaciones=implicaciones,
        contadores=contadores,
    )

    # 13. Fuentes (estructura compatible con redactor anterior)
    fuentes_usadas = sorted({h.get("fuente_url") for h in hallazgos if h.get("fuente_url")})
    fuentes_consultadas_base = sorted(set(resultado_busqueda.get("fuentes_consultadas_base") or []))
    fuentes = {
        "consultadas": fuentes_consultadas_base,
        "usadas": fuentes_usadas,
        "descartadas": sorted(set(fuentes_consultadas_base) - set(fuentes_usadas)),
    }

    # 14. Construir headlines compactas para que entrega.py arme el teaser
    headlines_for_teaser = _construir_headlines_para_teaser(hallazgos_ordenados, limite=4)

    # 15. Detalle de actores en silencio (códigos + nombres, no solo códigos)
    silencios_detalle = [
        {
            "codigo": a.get("codigo", ""),
            "nombre": a.get("nombre", ""),
            "cargo": a.get("cargo", ""),
        }
        for a in silencios
    ]

    return {
        "success": True,
        "informe_texto": texto_plano,
        "informe_html": html_completo,
        "fuentes": fuentes,
        "headlines_for_teaser": headlines_for_teaser,
        "metadata": {
            "version_redactor": "2.0-fase-b",
            "estado_general": estado,
            "correlativo": correlativo,
            "turno": turno,
            "rango_inicio": rango_inicio_iso,
            "rango_fin": rango_fin_iso,
            "contadores": contadores,
            "actores_catalogados": len(actores_index),
            "actores_en_silencio": [a.get("codigo") for a in silencios],
            "silencios_detalle": silencios_detalle,
            "hallazgos_contradictorios": len(contradicciones),
            "alertas_tempranas_sa": len(alertas_tempranas),
        },
    }


# =============================================================================
# Entry point manual
# =============================================================================


if __name__ == "__main__":
    # Ejecutar con un input mínimo de prueba
    ejemplo = {
        "rango_inicio": "2026-05-19T11:00:00+00:00",
        "rango_fin": "2026-05-19T18:00:00+00:00",
        "correlativo": "20260519-18H-180000",
        "turno": "cierre",
        "resultados": {},
        "fuentes_consultadas_base": [],
    }
    salida = redactar_informe(ejemplo)
    print("=== HTML ===")
    print(salida["informe_html"][:2000])
    print("\n=== TEXTO ===")
    print(salida["informe_texto"][:2000])
