"""
CENTINELA PRO — Buscador (Fase A)
==================================
Orquesta llamadas a Perplexity Sonar por submódulo estratégico, no por capa.
Inyecta toda la inteligencia metodológica del JSON v1.1 + catálogo de actores.

Arquitectura clave:
- Ciclo único diario (14:00 VET / 18:00 UTC): 15 calls, ventana fija de 24h
- Profile manual "matutino" disponible vía CENTINELA_PROFILE para uso ad-hoc (10 calls)
- Cada hallazgo se clasifica con alert_level (A0-A4) + opcional social_alert_level (SA0-SA4)
- Actores prioritarios se inyectan desde actores.json con código (GOB1, P1, INT1...)
- Compatibilidad preservada con monitor.py y redactor.py existentes

Decisión arquitectónica (2026-05-19, conversación con Morfe):
- Capa 3 desagregada (4 submódulos = 4 calls): oficialismo, oposición, AN, agenda nacional
  → cada uno alimenta una sección distinta del brief; consolidarlos pierde señal
- Capa 4 consolidada en 2 calls: occidente (Sudamérica+EEUU+Europa+Mercados),
  global sur (China+Rusia+India+Israel+Árabe+analistas)
  → alimentan la misma sección "lectura_internacional"; mantener 10 calls separadas
    saturaría el cupo sin ganancia analítica proporcional
- Capa 5 desagregada (2 calls): datos duros vs comunicados oficiales son tipos
  de evidencia con verification_status distintos
- Capas 1, 6, 7, 8, 9, 10 con 1 call cada una
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import requests

# =============================================================================
# Constantes y configuración
# =============================================================================

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
CONFIG_PATH = os.getenv(
    "CENTINELA_CONFIG_PATH",
    "config/monitor_noticias_multicapa_ve_v1_1.json",
)
ACTORES_PATH = os.getenv(
    "CENTINELA_ACTORES_PATH",
    "config/actores.json",
)

# Plan de llamadas — define qué submódulos del JSON v1.1 se consultan por profile.
# Cada entrada es UNA llamada a Perplexity. Cuando "submodules" tiene varios,
# se consolida en un único prompt (caso Capa 4 y Capa 1).
PLAN_BASE_MATUTINO: list[dict[str, Any]] = [
    # === Capa 3: prensa nacional, desagregada (4 calls) ===
    {"capa": 3, "submodules": ["oficialismo_y_estado"], "label": "oficialismo_estado"},
    {"capa": 3, "submodules": ["oposicion"], "label": "oposicion_venezolana"},
    {"capa": 3, "submodules": ["asamblea_nacional"], "label": "asamblea_nacional"},
    {"capa": 3, "submodules": ["agenda_nacional"], "label": "agenda_nacional"},
    # === Capa 4: internacional/geopolítica, consolidada en 2 calls ===
    {
        "capa": 4,
        "submodules": ["suramerica", "eeuu", "europa", "financieros_economicos"],
        "label": "internacional_occidente",
    },
    {
        "capa": 4,
        "submodules": ["china", "rusia", "analistas_rusos", "india", "israel", "arabe"],
        "label": "internacional_global_sur",
    },
    # === Capa 5: energía, desagregada (datos vs comunicados) ===
    {"capa": 5, "submodules": ["portales_energia"], "label": "energia_datos_mercado"},
    {"capa": 5, "submodules": ["comunicados_oficiales"], "label": "energia_comunicados_oficiales"},
    # === Capas sociales/OSINT (sin submódulos en el JSON) ===
    {"capa": 2, "submodules": [], "label": "redes_sociales_indirectas"},
    {"capa": 10, "submodules": [], "label": "telegram_osint_senales"},
]

PLAN_EXTRAS_CIERRE: list[dict[str, Any]] = [
    # Capa 1 consolidada: think tanks + universidades = analítico institucional
    {
        "capa": 1,
        "submodules": ["think_tanks", "universidades"],
        "label": "analitico_institucional",
    },
    # Capas 6, 7, 8, 9 (sin submódulos en el JSON)
    {"capa": 6, "submodules": [], "label": "ong_multilaterales"},
    {"capa": 7, "submodules": [], "label": "datos_estadisticas_indices"},
    {"capa": 8, "submodules": [], "label": "academica_papers"},
    {"capa": 9, "submodules": [], "label": "seguridad_defensa_osint"},
]

# Plan para modulo NACIONAL: solo capas nacionales (8 llamadas)
PLAN_NACIONAL: list[dict[str, Any]] = [
    # === Capa 3: prensa nacional, desagregada (4 calls) ===
    {"capa": 3, "submodules": ["oficialismo_y_estado"], "label": "oficialismo_estado"},
    {"capa": 3, "submodules": ["oposicion"], "label": "oposicion_venezolana"},
    {"capa": 3, "submodules": ["asamblea_nacional"], "label": "asamblea_nacional"},
    {"capa": 3, "submodules": ["agenda_nacional"], "label": "agenda_nacional"},
    # === Capa 5: energía nacional ===
    {"capa": 5, "submodules": ["comunicados_oficiales"], "label": "energia_comunicados_nacional"},
    # === Capas sociales/OSINT ===
    {"capa": 2, "submodules": [], "label": "redes_sociales_nacional"},
    {"capa": 10, "submodules": [], "label": "telegram_osint_nacional"},
    # === Capas datos/institucionales ===
    {"capa": 7, "submodules": [], "label": "datos_estadisticas_nacional"},
]

# Plan para modulo INTERNACIONAL: 8 llamadas por region
PLAN_INTERNACIONAL: list[dict[str, Any]] = [
    # === EE.UU. ===
    {
        "capa": 4,
        "submodules": ["eeuu"],
        "label": "eeuu",
        "fuentes_objetivo": [
            "reuters.com", "apnews.com", "nytimes.com", "washingtonpost.com",
            "state.gov", "home.treasury.gov/news"
        ]
    },
    # === Europa ===
    {
        "capa": 4,
        "submodules": ["europa"],
        "label": "europa",
        "fuentes_objetivo": [
            "bbc.com/news", "theguardian.com", "ft.com", "eeas.europa.eu"
        ]
    },
    # === America Latina ===
    {
        "capa": 4,
        "submodules": ["suramerica"],
        "label": "suramerica",
        "fuentes_objetivo": [
            "infobae.com", "elpais.com/america", "folha.uol.com.br", "eltiempo.com"
        ]
    },
    # === China/Rusia ===
    {
        "capa": 4,
        "submodules": ["china", "rusia"],
        "label": "china_rusia",
        "fuentes_objetivo": [
            "xinhuanet.com", "cgtn.com", "rt.com", "tass.com"
        ]
    },
    # === Mercado/Energia/Sanciones ===
    {
        "capa": 4,
        "submodules": ["financieros_economicos"],
        "label": "mercados_energia_sanciones",
        "fuentes_objetivo": [
            "bloomberg.com/energy", "reuters.com/business/energy",
            "opec.org/en/news", "eia.gov/petroleum",
            "home.treasury.gov/policy-issues/financial-sanctions"
        ]
    },
    # === multilaterales ===
    {
        "capa": 6,
        "submodules": [],
        "label": "multilaterales_internacional",
        "fuentes_objetivo": []
    },
    # === ongs ===
    {
        "capa": 7,
        "submodules": [],
        "label": "datos_internacional",
        "fuentes_objetivo": []
    },
    # === seguridad internacional ===
    {
        "capa": 9,
        "submodules": [],
        "label": "seguridad_internacional",
        "fuentes_objetivo": []
    },
]

# Plan para modulo ENERGIA: 5 llamadas especificas
PLAN_ENERGIA: list[dict[str, Any]] = [
    # === PDVSA y comunicados oficiales ===
    {
        "capa": 5,
        "submodules": ["comunicados_oficiales"],
        "label": "pdvsa_comunicados",
        "fuentes_objetivo": ["pdvsa.com"]
    },
    # === OPEP ===
    {
        "capa": 5,
        "submodules": ["portales_energia"],
        "label": "opep_oficial",
        "fuentes_objetivo": ["opec.org/en/news"]
    },
    # === IEA ===
    {
        "capa": 5,
        "submodules": ["portales_energia"],
        "label": "iea_datos",
        "fuentes_objetivo": ["iea.org"]
    },
    # === EIA ===
    {
        "capa": 5,
        "submodules": ["portales_energia"],
        "label": "eia_estadisticas",
        "fuentes_objetivo": ["eia.gov/petroleum"]
    },
    # === OFAC/Sanciones ===
    {
        "capa": 4,
        "submodules": ["financieros_economicos"],
        "label": "ofac_sanciones_energia",
        "fuentes_objetivo": [
            "home.treasury.gov/policy-issues/financial-sanctions"
        ]
    },
]

PLAN_LLAMADAS: dict[str, list[dict[str, Any]]] = {
    "matutino": PLAN_BASE_MATUTINO,
    "cierre": PLAN_BASE_MATUTINO + PLAN_EXTRAS_CIERRE,
    "nacional": PLAN_NACIONAL,
    "internacional": PLAN_INTERNACIONAL,
    "energia": PLAN_ENERGIA,
}

# Portales para Top 3 internacionales (P3)
TOP_INTERNACIONAL_PORTALS = [
    "reuters.com",
    "apnews.com",
    "afp.com",
    "bbc.com/news",
    "nytimes.com",
    "washingtonpost.com",
    "theguardian.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "aljazeera.com",
    "rt.com",
    "tass.com",
    "xinhuanet.com",
    "cgtn.com",
]

# Capas que deben llevar clasificación SA0-SA4 obligatoriamente
CAPAS_SOCIALES = {2, 10}

# =============================================================================
# Utilidades temporales y de turno
# =============================================================================


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _detectar_turno(ahora_utc: datetime) -> str:
    """
    Ciclo único diario a las 14:00 VET (18:00 UTC) → siempre "cierre" (15 calls).
    La función permanece para que CENTINELA_PROFILE pueda forzar "matutino" ad-hoc.
    """
    return "cierre"


def _profile_para_turno(turno: str) -> str:
    return turno if turno in PLAN_LLAMADAS else "cierre"


def _calcular_rango(
    ahora_utc: datetime,
    horas_atras: int | None,
    turno: str,
) -> tuple[datetime, datetime]:
    """Ventana fija de 24h siempre."""
    if horas_atras is None:
        horas_atras = 24
    horas_atras = max(24, min(int(horas_atras), 72))  # Minimo 24h
    inicio = ahora_utc - timedelta(hours=horas_atras)
    return inicio, ahora_utc


def _ajustar_filtros_fecha(inicio: datetime, fin: datetime) -> tuple[str, str]:
    """
    after = inicio - 1 día (margen para noticias fechadas por día sin hora exacta).
    before = fin + 1 día ('before' es exclusivo en buscadores web).
    """
    after_date = inicio.date() - timedelta(days=1)
    before_date = fin.date() + timedelta(days=1)
    return after_date.strftime("%Y-%m-%d"), before_date.strftime("%Y-%m-%d")


def _correlativo(fecha_utc: datetime, turno: str) -> str:
    sufijo = "11H" if turno == "matutino" else "14H"
    return f"{fecha_utc.strftime('%Y%m%d')}-{sufijo}-{fecha_utc.strftime('%H%M%S')}"


_MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _fecha_humana(dt: datetime) -> str:
    """Ej: '24 de mayo de 2026'. Perplexity ancla mejor con fechas en lenguaje natural."""
    return f"{dt.day} de {_MESES_ES[dt.month - 1]} de {dt.year}"


# =============================================================================
# Carga de configuración y catálogos
# =============================================================================


def _cargar_json_archivo(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[buscador] JSON corrupto en {path_str}: {exc} (línea {exc.lineno}, col {exc.colno})")
        return {}
    except Exception as exc:
        print(f"[buscador] Error leyendo {path_str}: {exc}")
        return {}


def _cargar_config() -> dict[str, Any]:
    return _cargar_json_archivo(CONFIG_PATH)


def _cargar_actores() -> dict[str, Any]:
    return _cargar_json_archivo(ACTORES_PATH)


def _indexar_actores(actores_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Indexa por código de actor (GOB1, P1, INT1...) para acceso rápido."""
    index: dict[str, dict[str, Any]] = {}
    for actor in actores_data.get("actores", []):
        codigo = actor.get("codigo")
        if codigo:
            index[codigo] = actor
    return index


def _obtener_codigos_validos(actores_data: dict[str, Any]) -> list[str]:
    """Obtiene la lista de códigos de actores válidos para inyectar en el prompt."""
    codigos = []
    for actor in actores_data.get("actores", []):
        codigo = actor.get("codigo")
        if codigo:
            codigos.append(codigo)
    return sorted(codigos)


def _actores_relevantes_para_tarea(
    tarea: dict[str, Any],
    actores_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Devuelve la lista de actores a inyectar en el prompt de esta tarea.

    Lógica:
    - Capas sociales/OSINT (2, 10): se inyectan TODOS los actores de prioridad alta,
      independientemente de su submódulo. Esto es importante porque las señales
      sociales pueden involucrar a cualquier actor relevante, no solo a los del
      submódulo de su bloque.
    - Resto de capas: se filtran solo los actores cuyo `submodulo_principal`
      coincide con algún submódulo de la tarea.
    - Capas sin submódulos que no son sociales (6, 7, 8, 9): no inyectan actores
      (consultas más estructurales).
    """
    if not actores_data:
        return []

    capa_id = tarea.get("capa")
    submodulos = set(tarea.get("submodules", []))
    actores = actores_data.get("actores", []) or []

    # Capas sociales/OSINT: inyectar actores de alta prioridad
    if capa_id in CAPAS_SOCIALES:
        return [a for a in actores if a.get("prioridad_monitoreo") == "alta"]

    # Resto: filtrar por submódulo principal
    if not submodulos:
        return []
    return [a for a in actores if a.get("submodulo_principal") in submodulos]


def _capa_por_id(config: dict[str, Any], capa_id: int) -> dict[str, Any] | None:
    for capa in config.get("layers", []) or []:
        if capa.get("layer_id") == capa_id:
            return capa
    return None


def _recolectar_directrices(
    capa: dict[str, Any],
    submodule_names: list[str],
) -> dict[str, Any]:
    """
    Recolecta del JSON v1.1: priority_questions, query_templates, selection_rules,
    extract_fields y what_to_search, fusionando capa + submódulos solicitados.
    """
    queries: list[str] = []
    priority_questions: list[str] = list(capa.get("priority_questions") or [])
    selection_rules: list[str] = list(capa.get("selection_rules") or [])
    extract_fields: list[str] = list(capa.get("extract_fields") or [])
    what_to_search: list[str] = list(capa.get("what_to_search") or [])

    # Queries directos en la capa (cuando no tiene submódulos)
    queries.extend(capa.get("query_templates") or [])

    # Recorrer submódulos solicitados
    submodulos_capa = {s.get("name"): s for s in (capa.get("submodules") or [])}
    for name in submodule_names:
        sub = submodulos_capa.get(name)
        if not sub:
            continue
        queries.extend(sub.get("query_templates") or [])
        extract_fields.extend(sub.get("extract_fields") or [])
        what_to_search.extend(sub.get("what_to_search") or [])
        # selection_rules específicas del submódulo también suman
        selection_rules.extend(sub.get("selection_rules") or [])

    return {
        "queries": list(dict.fromkeys(queries)),
        "priority_questions": list(dict.fromkeys(priority_questions)),
        "selection_rules": list(dict.fromkeys(selection_rules)),
        "extract_fields": list(dict.fromkeys(extract_fields)),
        "what_to_search": list(dict.fromkeys(what_to_search)),
    }


def _resumen_fuentes_registry(config: dict[str, Any]) -> str:
    """Devuelve top fuentes del source_registry, formateadas para prompt."""
    registry = config.get("source_registry") or []
    if not registry:
        return ""
    partes = []
    for fuente in registry[:12]:
        nombre = fuente.get("name") or fuente.get("domain")
        tipo = fuente.get("source_type") or ""
        if nombre:
            partes.append(f"- {nombre} ({tipo})")
    return "\n".join(partes)


# =============================================================================
# Construcción del prompt
# =============================================================================


def _build_top_internacional_prompt(
    fechas: dict[str, str],
    portales: list[str],
) -> str:
    """Prompt para extraer 3 titulares principales de cada portal (P3)."""
    portales_str = "\n".join(f"- {p}" for p in portales)
    fecha_humana = fechas.get("fecha_humana", "hoy")

    return f"""Eres un extractor de titulares internacionales de precisión.
Devuelve JSON estricto sin preámbulo ni texto adicional.

OBJETIVO:
Extraer EXACTAMENTE los **3 titulares más importantes del día ({fecha_humana})** 
de CADA portal de la lista. No filtres por tema, país o palabra clave.

PORTALS A CONSULTAR:
{portales_str}

FORMATO DE RESPUESTA OBLIGATORIO:
{{
  "top_internacional": {{
    "reuters.com": [
      {{"titulo": "Título 1", "url": "https://...", "fecha_hora_utc": "YYYY-MM-DDTHH:MM:SSZ"}},
      {{"titulo": "Título 2", "url": "https://...", "fecha_hora_utc": "YYYY-MM-DDTHH:MM:SSZ"}},
      {{"titulo": "Título 3", "url": "https://...", "fecha_hora_utc": "YYYY-MM-DDTHH:MM:SSZ"}}
    ],
    "bbc.com/news": [...]
  }}
}}

REGLAS ABSOLUTAS:
1. EXACTAMENTE 3 titulares por portal (los más relevantes/visibles en portada)
2. Extrae de homepage o sección "Top News"/"Latest"
3. URL debe ser completa y verificable
4. Si un portal no tiene noticias, devuelve [] para ese portal
5. NO apliques ningún filtro temático (Venezuela, América, etc.)
6. Prioriza: política > economía > energía > seguridad > otros
7. Si un titular no tiene hora exacta, usa fecha del día + hora estimada
"""


def _build_prompt(
    *,
    capa: dict[str, Any],
    tarea: dict[str, Any],
    directrices: dict[str, Any],
    actores_relevantes: list[dict[str, Any]],
    fechas: dict[str, str],
    config: dict[str, Any],
    codigos_validos: list[str] | None = None,
    fuentes_objetivo: list[str] | None = None,
) -> str:
    """Construye el prompt completo con toda la inteligencia metodológica del JSON v1.1."""

    layer_name = capa.get("name", f"layer_{capa.get('layer_id')}")
    layer_id = capa.get("layer_id")
    mission = capa.get("mission", "")
    is_social = layer_id in CAPAS_SOCIALES

    submodulos_str = ", ".join(tarea.get("submodules", [])) or "(capa sin submódulos)"

    priority_questions = directrices.get("priority_questions", [])
    what_to_search = directrices.get("what_to_search", [])
    queries = directrices.get("queries", [])
    selection_rules = directrices.get("selection_rules", [])

    # --- Bloques condicionales del prompt ---
    questions_block = ""
    if priority_questions:
        questions_block = "\n## PREGUNTAS PRIORITARIAS:\n" + "\n".join(
            f"- {q}" for q in priority_questions
        )

    what_block = ""
    if what_to_search:
        what_block = "\n## QUÉ BUSCAR ESPECÍFICAMENTE:\n" + "\n".join(
            f"- {w}" for w in what_to_search[:12]
        )

    queries_block = ""
    if queries:
        queries_block = "\n## CONSULTAS DE REFERENCIA (úsalas como guía, no literales):\n" + "\n".join(
            f"- {q}" for q in queries[:15]
        )

    rules_block = ""
    if selection_rules:
        rules_block = "\n## REGLAS DE SELECCIÓN:\n" + "\n".join(
            f"- {r}" for r in selection_rules[:10]
        )

    actores_block = ""
    if actores_relevantes:
        actores_block = (
            "\n## ACTORES DE MONITOREO PRIORITARIO\n"
            "Si encuentras hallazgos sobre estos actores, usa el CÓDIGO en `actor_principal`:\n"
        )
        for a in actores_relevantes:
            cargo = a.get("cargo", "")
            # Render handles de redes sociales si están registrados
            redes = a.get("redes_sociales") or {}
            handles_parts = []
            if redes.get("x"):
                handles_parts.append(f"X: @{redes['x'].lstrip('@')}")
            if redes.get("instagram"):
                handles_parts.append(f"IG: @{redes['instagram'].lstrip('@')}")
            handles_str = f" — {' / '.join(handles_parts)}" if handles_parts else ""
            actores_block += f"- `{a['codigo']}` — {a['nombre']} ({cargo}){handles_str}\n"

    # --- Bloque de códigos válidos (P4: evitar códigos inventados) ---
    codigos_block = ""
    if codigos_validos:
        codigos_str = ", ".join(codigos_validos)
        codigos_block = f"""
## CÓDIGOS DE ACTOR VÁLIDOS

La lista CERRADA de códigos de actor permitidos es: {codigos_str}.

**REGLAS CRÍTICAS SOBRE ACTORES:**
- El campo `actor_principal` DEBE ser uno de los códigos de la lista anterior O el string vacío `''`.
- Si el actor del hallazgo NO está en el catálogo, deja `actor_principal=''` y pon el nombre completo en `actor_nombre`.
- NUNCA inventes códigos de actor. Si no estás seguro, usa el nombre completo en `actor_nombre` y deja `actor_principal=''`.
- NUNCA uses valores como 'EEUU', 'LabPaz', 'NA', 'FMI', 'Gobierno de Venezuela', 'ONG/DDHH' como códigos.
"""

    # --- Bloque de fuentes objetivo para módulo internacional (BUG 6) ---
    fuentes_objetivo_block = ""
    if fuentes_objetivo:
        portales_str = "\n".join(f"- {p}" for p in fuentes_objetivo)
        fuentes_objetivo_block = f"""
## DOMINIOS OBJETIVO (BUSCAR ESPECÍFICAMENTE EN ESTOS DOMINIOS)

{portales_str}

**INSTRUCCIONES:**
- Busca específicamente en estos dominios.
- Para cada hallazgo devuelve la URL exacta del artículo (no la homepage).
- Top 3 artículos más relevantes de las últimas 24h en esos dominios.
- Si no encuentras nada relevante en estos dominios, devuelve hallazgos:[] y explica en notas qué encontraste.
"""

    fuentes_block = ""
    fuentes_str = _resumen_fuentes_registry(config)
    if fuentes_str:
        fuentes_block = (
            "\n## FUENTES PRIORITARIAS (preferir cuando estén disponibles):\n" + fuentes_str
        )

    # --- Sistema de clasificación A4-A0 ---
    a_levels_block = """
## SISTEMA DE CLASIFICACIÓN A4-A0 (urgencia + impacto decisional)
Aplica a TODOS los hallazgos:
- **A4** alerta prioritaria: impacto decisional inmediato, máxima urgencia (respuesta 0-3h)
- **A3** señal estratégica: anuncios institucionales clave, decisiones importantes
- **A2** narrativa relevante: tendencias políticas, posiciones discursivas, marco
- **A1** reacción menor: hechos secundarios, contexto explicativo
- **A0** actividad rutinaria: solo registro, sin acción
"""

    # --- SA0-SA4 si capa social ---
    sa_block = ""
    if is_social:
        sa_block = """
## CLASIFICACIÓN ADICIONAL SA4-SA0 (OBLIGATORIA para esta capa social/OSINT)
- **SA4**: confirmado por fuente oficial o múltiples fuentes primarias independientes
- **SA3**: señal verosímil, evidencia OSINT o cobertura secundaria confiable
- **SA2**: alerta en observación; señal repetida sin confirmación primaria
- **SA1**: mención aislada sin respaldo independiente — NO tratar como hecho
- **SA0**: descartado por irrelevante, spam o duplicado

CRÍTICO: las señales SA1-SA3 nunca se redactan como hechos confirmados.
Toda señal social debe llevar `warning_label` con texto explícito de advertencia.
"""

    # --- JSON schema de salida ---
    json_schema_block = """
## FORMATO DE RESPUESTA OBLIGATORIO
Devuelve JSON estricto. Sin texto antes ni después. Sin bloque markdown ```.

{
  "hallazgos": [
    {
      "titulo": "encabezado breve y específico",
      "fact_summary": "una frase con el hecho central, verificable",
      "fecha_hora_utc": "YYYY-MM-DDTHH:MM:SSZ",
      "actor_principal": "código del catálogo (ej: GOB1, P1, INT1) o nombre si no está catalogado",
      "ubicacion": "ciudad/país relevante",
      "categoria": "nacional|internacional|geopolitica|politica|economia|ddhh|energia|hidrocarburos|seguridad|otros",
      "alert_level": "A4|A3|A2|A1|A0",
      "alert_level_motivo": "justificación breve del nivel asignado",
      "social_alert_level": null,
      "warning_label": null,
      "fuente_nombre": "nombre del medio o emisor",
      "fuente_url": "https://... URL verificable",
      "fuente_url_secundaria": null,
      "source_type": "official|state_media|independent_media|opposition_actor|multilateral|ngo|think_tank|academic|market_data|social_signal|telegram_signal|osint|opinion|aggregator",
      "verification_status": "officially_confirmed|cross_checked|single_source|unverified|contradicted",
      "why_it_matters": "implicación operativa o estratégica en 1-2 frases",
      "data_points": [],
      "entities_detected": []
    }
  ],
  "fuentes_consultadas": ["lista de URLs base consultadas"],
  "notas": "limitaciones, ausencia de hallazgos, observaciones metodológicas"
}

REGLAS DE LLENADO:
- En capas sociales (2 y 10): `social_alert_level` OBLIGATORIO y `warning_label` obligatorio si <SA4.
- En capas no sociales: `social_alert_level=null` y `warning_label=null`.
- Si encuentras segunda fuente confirmando el mismo hecho, llena `fuente_url_secundaria`.
- `data_points`: solo cifras concretas (porcentajes, montos, fechas precisas, no narrativa).
- `entities_detected`: organizaciones, lugares y personas adicionales (no el actor principal).
- No reproduzcas texto literal extenso de artículos. Parafrasea siempre.
"""

    fecha_humana = fechas.get("fecha_humana", "hoy")

    prompt = f"""Eres un asistente de monitoreo de inteligencia política venezolana de alta precisión. Devuelves hallazgos verificables, fechados y con URL. No inventas datos. Si no hay evidencia suficiente, lo dices en `notas`.

# CONTEXTO DE LA BÚSQUEDA
- Capa: **{layer_name}** (Capa {layer_id})
- Submódulos activos: {submodulos_str}
- Misión de esta capa: {mission}
- País foco: Venezuela
- Fecha de referencia: **{fecha_humana}** (hoy en Venezuela)
- Rango de interés: {fechas['rango_inicio']} a {fechas['rango_fin']}
- Filtro fecha recomendado: `after:{fechas['after']} before:{fechas['before']}`

# REGLAS CRÍTICAS NO NEGOCIABLES
1. Busca noticias publicadas HOY {fecha_humana} o en las últimas 24 horas. Acepta articulos fechados por día aunque no tengan hora exacta. Descarta solo contenido claramente anterior a 24 horas o sin fecha verificable.
2. Cita siempre URL verificable. Sin URL = no entra al resultado.
3. Clasifica TODOS los hallazgos con `alert_level` (A0-A4).
4. Si el actor coincide con el catálogo, usa el CÓDIGO en `actor_principal`. Si no está en el catálogo, deja `actor_principal=''` y usa el nombre en `actor_nombre`.
5. No reproduzcas texto literal de los artículos. Parafrasea.
6. Si no hay hallazgos verificables, devuelve "hallazgos": [] y explica en notas.
7. **FECHAS (P5):** Si NO puedes verificar la fecha de publicación de la fuente, deja `fecha_hora_utc=null`. NO uses fecha actual ni fecha del corte como sustituto.
{questions_block}{what_block}{queries_block}{rules_block}{actores_block}{codigos_block}{fuentes_objetivo_block}{fuentes_block}
{a_levels_block}{sa_block}
{json_schema_block}
"""
    return prompt


# =============================================================================
# Llamada a Perplexity y parseo
# =============================================================================


def _consulta_perplexity(prompt: str, timeout: int) -> dict[str, Any]:
    api_key = os.getenv("PERPLEXITY_API_KEY", "").strip()
    if not api_key:
        return {"success": False, "error": "PERPLEXITY_API_KEY no configurada", "texto": ""}

    recency = os.getenv("PERPLEXITY_RECENCY", "week")
    payload = {
        "model": os.getenv("PERPLEXITY_MODEL", "sonar"),
        "search_recency_filter": recency,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres un asistente de monitoreo de inteligencia política venezolana. "
                    "Devuelves SIEMPRE JSON estricto sin preámbulos ni texto adicional. "
                    "No inventas datos. Si no hay hallazgos, devuelves hallazgos:[] con notas."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }

    try:
        response = requests.post(
            PERPLEXITY_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        texto = ""
        for choice in data.get("choices", []):
            contenido = (choice.get("message") or {}).get("content", "")
            if contenido:
                texto += f"\n{contenido}" if texto else contenido
        return {"success": True, "texto": texto.strip(), "raw": data}
    except Exception as exc:
        return {"success": False, "error": str(exc), "texto": ""}


def _intentar_parsear_json(texto: str) -> dict[str, Any] | None:
    """Parsea JSON tolerante a wrapping en markdown o preámbulos del modelo."""
    if not texto:
        return None
    # Limpiar wrapping markdown ```json ... ```
    texto_limpio = re.sub(r"^```(?:json)?\s*", "", texto.strip())
    texto_limpio = re.sub(r"\s*```$", "", texto_limpio)
    try:
        return json.loads(texto_limpio)
    except Exception:
        # Fallback: buscar el primer objeto JSON balanceado en el texto
        match = re.search(r"\{[\s\S]*\}", texto_limpio)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
        return None


# =============================================================================
# Archivado (preservado del original, mejorado)
# =============================================================================


def _archivado_habilitado() -> bool:
    return os.getenv("ARCHIVE_URLS", "1").strip().lower() in {"1", "true", "yes", "si"}


def _archive_limit() -> int:
    raw = os.getenv("ARCHIVE_LIMIT", "12").strip()
    return max(1, int(raw)) if raw.isdigit() else 12


def _archivar_url(url: str, timeout: int) -> dict[str, str]:
    resultados: dict[str, str] = {}
    if not url:
        return resultados

    try:
        response = requests.get(
            f"https://web.archive.org/save/{quote(url, safe='')}",
            timeout=timeout,
            allow_redirects=True,
        )
        if response.status_code == 200:
            resultados["wayback"] = response.url
        else:
            resultados["wayback_error"] = f"HTTP {response.status_code}"
    except Exception as exc:
        resultados["wayback_error"] = str(exc)

    try:
        response = requests.post(
            "https://archive.today/submit/",
            data={"url": url, "anyway": "1"},
            timeout=timeout,
            allow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (centinela archive bot)"},
        )
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if location:
                resultados["archive_today"] = urljoin(response.url, location)
        elif response.status_code == 200:
            refresh = response.headers.get("Refresh", "")
            match = re.search(r"\burl\s*=\s*(.+)", refresh, re.IGNORECASE)
            if match:
                resultados["archive_today"] = urljoin(
                    response.url, match.group(1).strip().strip("\"'")
                )
        if "archive_today" not in resultados and "archive_today_error" not in resultados:
            resultados["archive_today_error"] = f"HTTP {response.status_code}"
    except Exception as exc:
        resultados["archive_today_error"] = str(exc)

    return resultados


def _archivar_hallazgos(parsed: dict[str, Any] | None) -> None:
    if not parsed or not _archivado_habilitado():
        return
    hallazgos = parsed.get("hallazgos")
    if not isinstance(hallazgos, list):
        return

    limite = _archive_limit()
    timeout = int(os.getenv("ARCHIVE_TIMEOUT", "35"))
    archivados = 0

    for item in hallazgos:
        if archivados >= limite:
            break
        if not isinstance(item, dict):
            continue
        url = item.get("fuente_url")
        if not url or item.get("archivos"):
            continue
        archivos = _archivar_url(url, timeout=timeout)
        item["archivos"] = archivos
        item["archivo_timestamp_utc"] = _utc_now().isoformat()
        archivados += 1


# =============================================================================
# Anotación y defaults de hallazgos
# =============================================================================


def _anotar_resultados(
    parsed: dict[str, Any] | None,
    tarea: dict[str, Any],
    capa: dict[str, Any],
) -> None:
    """Agrega metadatos de tarea/capa y aplica defaults a hallazgos."""
    if not parsed:
        return
    hallazgos = parsed.get("hallazgos")
    if not isinstance(hallazgos, list):
        return

    capa_id = capa.get("layer_id")
    capa_label = f"capa_{capa_id}"
    capa_desc = f"{capa.get('name','')}. {capa.get('mission','')}".strip(". ")

    for item in hallazgos:
        if not isinstance(item, dict):
            continue
        # Metadatos de origen
        item.setdefault("capa", capa_label)
        item.setdefault("capa_descripcion", capa_desc)
        item.setdefault("submodules_consultados", list(tarea.get("submodules", [])))
        item.setdefault("tarea_label", tarea.get("label"))
        # Defaults defensivos si el modelo omitió campos críticos
        item.setdefault("alert_level", "A2")
        item.setdefault("verification_status", "single_source")
        item.setdefault("source_type", "independent_media")
        item.setdefault("social_alert_level", None)
        item.setdefault("warning_label", None)
        item.setdefault("fuente_url_secundaria", None)
        item.setdefault("data_points", [])
        item.setdefault("entities_detected", [])


# =============================================================================
# Función principal
# =============================================================================


def buscar_noticias(horas_atras: int | None = None) -> dict[str, Any]:
    """
    Orquesta la búsqueda multinivel.
    Retorna un dict con la misma forma que monitor.py y redactor.py esperan,
    enriquecido con la nueva estructura por-tarea y metadatos del catálogo.
    """
    ahora = _utc_now()
    turno = _detectar_turno(ahora)

    # Profile override por env (útil para workflow_dispatch manual)
    profile_env = os.getenv("CENTINELA_PROFILE", "").strip().lower()
    profile = profile_env if profile_env in PLAN_LLAMADAS else _profile_para_turno(turno)

    inicio, fin = _calcular_rango(ahora, horas_atras, turno)
    correlativo = _correlativo(fin, turno)
    after, before = _ajustar_filtros_fecha(inicio, fin)
    timeout = int(os.getenv("PERPLEXITY_TIMEOUT", "45"))

    fechas = {
        "rango_inicio": inicio.isoformat(),
        "rango_fin": fin.isoformat(),
        "after": after,
        "before": before,
        "fecha_humana": _fecha_humana(fin),
    }

    config = _cargar_config()
    actores_data = _cargar_actores()
    actores_index = _indexar_actores(actores_data)

    if not config:
        return {
            "success": False,
            "error": f"Config no encontrada en {CONFIG_PATH}",
            "turno": turno,
            "profile": profile,
            "correlativo": correlativo,
            "resultados": {},
            "errores": [f"Config no encontrada en {CONFIG_PATH}"],
        }

    plan = PLAN_LLAMADAS[profile]
    resultados_por_tarea: dict[str, Any] = {}
    errores: list[str] = []

    for tarea in plan:
        capa = _capa_por_id(config, tarea["capa"])
        if not capa:
            errores.append(f"Capa {tarea['capa']} no existe en config")
            continue

        directrices = _recolectar_directrices(capa, tarea["submodules"])
        actores_relevantes = _actores_relevantes_para_tarea(tarea, actores_data)
        codigos_validos = _obtener_codigos_validos(actores_data)

        fuentes_objetivo = tarea.get("fuentes_objetivo")
        prompt = _build_prompt(
            capa=capa,
            tarea=tarea,
            directrices=directrices,
            actores_relevantes=actores_relevantes,
            fechas=fechas,
            config=config,
            codigos_validos=codigos_validos,
            fuentes_objetivo=fuentes_objetivo,
        )

        salida = _consulta_perplexity(prompt, timeout)
        parsed = _intentar_parsear_json(salida.get("texto", ""))
        _anotar_resultados(parsed, tarea, capa)
        _archivar_hallazgos(parsed)
        salida["parsed"] = parsed
        salida["tarea"] = {
            "capa": tarea["capa"],
            "submodules": tarea["submodules"],
            "label": tarea["label"],
            "actores_inyectados": [a.get("codigo") for a in actores_relevantes],
        }

        # Clave única por tarea (capa + label) para no colisionar con redactor
        clave = f"capa_{tarea['capa']}__{tarea['label']}"
        resultados_por_tarea[clave] = salida

        if not salida.get("success"):
            errores.append(f"{clave}: {salida.get('error', 'Error desconocido')}")

    # Lista de fuentes base, compatible con campo esperado por redactor.py
    fuentes_consultadas_base = [
        f.get("name") for f in (config.get("source_registry") or []) if f.get("name")
    ]

    # === P3: Llamada para top 3 titulares internacionales ===
    top_internacional_result = _consulta_perplexity(
        _build_top_internacional_prompt(fechas, TOP_INTERNACIONAL_PORTALS),
        timeout=60
    )
    top_internacional_parsed = _intentar_parsear_json(
        top_internacional_result.get("texto", "")
    ) or {"top_internacional": {}}

    return {
        "success": len(errores) < len(plan),
        "turno": turno,
        "profile": profile,
        "correlativo": correlativo,
        "rango_inicio": fechas["rango_inicio"],
        "rango_fin": fechas["rango_fin"],
        "after": after,
        "before": before,
        "plan_tareas": [
            {"capa": t["capa"], "submodules": t["submodules"], "label": t["label"]}
            for t in plan
        ],
        "resultados": resultados_por_tarea,
        "errores": errores,
        "fuentes_consultadas_base": fuentes_consultadas_base,
        "actores_catalogados": len(actores_index),
        "total_llamadas_ejecutadas": len(resultados_por_tarea),
        # P3: Top 3 titulares por portal internacional
        "top_internacional": top_internacional_parsed.get("top_internacional", {}),
    }


# =============================================================================
# Entry point manual / debugging
# =============================================================================


if __name__ == "__main__":
    horas_raw = os.getenv("HORAS_ATRAS", "").strip()
    horas = int(horas_raw) if horas_raw.isdigit() else None
    salida = buscar_noticias(horas)
    print(json.dumps(salida, ensure_ascii=False, indent=2))
