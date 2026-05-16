from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import requests

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

PROMPT_SISTEMA = (
    "Redacta un informe periodístico verificable, claro y estructurado. "
    "No inventes datos. Si no hay evidencia suficiente, dilo explícitamente. "
    "Usa un tono profesional y evita conclusiones sin respaldo."
)

SECCIONES = [
    "Nacional",
    "Internacional / Geopolítica",
    "Política",
    "Economía",
    "Energética / Hidrocarburos",
    "DDHH",
    "Seguridad",
    "Otros",
]

RELEVANCIA_ORDEN = {"baja": 1, "media": 2, "alta": 3}


def _merge_pesos(base: dict[str, int], env_name: str, lower_keys: bool = False) -> dict[str, int]:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return base
    try:
        data = json.loads(raw)
    except Exception:
        return base
    if not isinstance(data, dict):
        return base
    merged = dict(base)
    for key, value in data.items():
        if not isinstance(value, (int, float)):
            continue
        norm_key = str(key).lower() if lower_keys else str(key)
        merged[norm_key] = int(value)
    return merged


_CAPA_PESO_BASE = {
    "capa_1": 3,  # redes directas
    "capa_2": 2,  # redes indirectas
    "capa_3": 2,  # prensa nacional/regional
    "capa_4": 2,  # internacional/geopolítica
    "capa_5": 2,  # energía/hidrocarburos
    "capa_6": 2,  # ONG/multilaterales
}

_CATEGORIA_PESO_BASE = {
    "Nacional": 3,
    "Política": 3,
    "Economía": 2,
    "Internacional / Geopolítica": 2,
    "Energética / Hidrocarburos": 2,
    "DDHH": 2,
    "Seguridad": 2,
    "Otros": 1,
}

_ACTOR_PESO_BASE = {
    "gobierno": 3,
    "presidencia": 3,
    "maduro": 3,
    "oposicion": 2,
    "oposición": 2,
    "asamblea": 2,
    "tsj": 2,
    "cne": 2,
    "pdh": 2,
    "petróleo": 2,
    "petroleo": 2,
}

CAPA_PESO = _merge_pesos(_CAPA_PESO_BASE, "CAPA_PESO_JSON", lower_keys=True)
CATEGORIA_PESO = _merge_pesos(_CATEGORIA_PESO_BASE, "CATEGORIA_PESO_JSON")
ACTOR_PESO = _merge_pesos(_ACTOR_PESO_BASE, "ACTOR_PESO_JSON", lower_keys=True)


def _parse_datetime(valor: str) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extraer_texto_respuesta(data: dict[str, Any]) -> str:
    choices = data.get("choices", [])
    if choices and isinstance(choices, list):
        content = (choices[0].get("message") or {}).get("content", "")
        if content:
            return content.strip()
    return ""


def _relevancia_valor(valor: str | None) -> int:
    if not valor:
        return 0
    return RELEVANCIA_ORDEN.get(valor.strip().lower(), 0)


def _relevancia_minima() -> int:
    minimo = os.getenv("RELEVANCIA_MINIMA", "media").strip().lower()
    return RELEVANCIA_ORDEN.get(minimo, 2)


def _min_por_seccion() -> int:
    raw = os.getenv("MIN_HALLAZGOS_SECCION", "1").strip()
    return max(1, int(raw)) if raw.isdigit() else 1


def _capa_peso(capa: str | None) -> int:
    if not capa:
        return 0
    return CAPA_PESO.get(str(capa).strip().lower(), 1)


def _actor_peso(actor: str | None) -> int:
    if not actor:
        return 0
    actor_norm = actor.strip().lower()
    for clave, peso in ACTOR_PESO.items():
        if clave in actor_norm:
            return peso
    return 0


def _categoria_peso(categoria: str | None) -> int:
    if not categoria:
        return 0
    return CATEGORIA_PESO.get(_normalizar_categoria(categoria), 1)


def _dedupe_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("fuente_url", "")).strip(), str(item.get("titulo", "")).strip())


def _fecha_sort_key(item: dict[str, Any]) -> datetime:
    fecha = item.get("fecha_hora_utc")
    parsed = _parse_datetime(fecha) if isinstance(fecha, str) else None
    return parsed or datetime.min


def _extraer_hallazgos(resultado_busqueda: dict[str, Any]) -> list[dict[str, Any]]:
    hallazgos: list[dict[str, Any]] = []
    minimo = _relevancia_minima()
    vistos: set[tuple[str, str]] = set()

    for capa_data in (resultado_busqueda.get("resultados") or {}).values():
        parsed = (capa_data or {}).get("parsed") or {}
        for item in parsed.get("hallazgos", []) or []:
            if not isinstance(item, dict):
                continue
            if _relevancia_valor(item.get("relevancia")) < minimo:
                continue
            if not item.get("fuente_url"):
                continue
            if not item.get("titulo"):
                continue
            key = _dedupe_key(item)
            if key in vistos:
                continue
            vistos.add(key)
            hallazgos.append(item)

    hallazgos.sort(
        key=lambda x: (
            _relevancia_valor(x.get("relevancia")),
            _capa_peso(x.get("capa")),
            _categoria_peso(x.get("categoria")),
            _actor_peso(x.get("actor_principal")),
            _fecha_sort_key(x),
        ),
        reverse=True,
    )
    return hallazgos


def _extraer_inventarios(resultado_busqueda: dict[str, Any]) -> list[dict[str, Any]]:
    inventarios: list[dict[str, Any]] = []
    for capa_data in (resultado_busqueda.get("resultados") or {}).values():
        parsed = (capa_data or {}).get("parsed") or {}
        inv = parsed.get("inventario_auditoria")
        if isinstance(inv, dict):
            inventarios.append(inv)
    return inventarios


def _normalizar_categoria(cat: str | None) -> str:
    if not cat:
        return "Otros"
    cat_norm = cat.strip().lower()
    mapping = {
        "nacional": "Nacional",
        "internacional": "Internacional / Geopolítica",
        "geopolitica": "Internacional / Geopolítica",
        "geopolítica": "Internacional / Geopolítica",
        "politica": "Política",
        "política": "Política",
        "economia": "Economía",
        "economía": "Economía",
        "energetica": "Energética / Hidrocarburos",
        "energética": "Energética / Hidrocarburos",
        "hidrocarburos": "Energética / Hidrocarburos",
        "ddhh": "DDHH",
        "seguridad": "Seguridad",
        "otros": "Otros",
    }
    return mapping.get(cat_norm, "Otros")


def _agrupar_por_seccion(hallazgos: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    agrupado = {s: [] for s in SECCIONES}
    for item in hallazgos:
        categoria = _normalizar_categoria(item.get("categoria"))
        agrupado.setdefault(categoria, []).append(item)
    return agrupado


def _secciones_omitidas(hallazgos: list[dict[str, Any]], min_por_seccion: int) -> list[dict[str, Any]]:
    por_seccion = _agrupar_por_seccion(hallazgos)
    omitidas: list[dict[str, Any]] = []
    for seccion in SECCIONES:
        items = por_seccion.get(seccion) or []
        if 0 < len(items) < min_por_seccion:
            omitidas.append({"seccion": seccion, "hallazgos": len(items)})
    return omitidas


def _construir_contexto(resultado_busqueda: dict[str, Any]) -> str:
    hallazgos = _extraer_hallazgos(resultado_busqueda)
    if not hallazgos:
        return ""

    min_por_seccion = _min_por_seccion()
    por_seccion = _agrupar_por_seccion(hallazgos)
    partes: list[str] = []
    for seccion in SECCIONES:
        items = por_seccion.get(seccion) or []
        if len(items) < min_por_seccion:
            continue
        partes.append(f"## {seccion}")
        for item in items:
            partes.append(
                f"- Fecha UTC: {item.get('fecha_hora_utc','')} | {item.get('titulo','')}\n"
                f"  Resumen: {item.get('resumen_1_frase','')}\n"
                f"  Actor: {item.get('actor_principal','')} | Ubicación: {item.get('ubicacion','')}\n"
                f"  Relevancia: {item.get('relevancia','')} | Motivo: {item.get('relevancia_motivo','')}\n"
                f"  Fuente: {item.get('fuente_nombre','')} | {item.get('fuente_url','')}\n"
                f"  Capa: {item.get('capa','')} | {item.get('capa_descripcion','')}\n"
                f"  Link de verificación: {item.get('fuente_url','')}"
            )
    return "\n".join(partes).strip()


def _fuentes_desde_resultados(resultado_busqueda: dict[str, Any]) -> dict[str, list[str]]:
    fuentes_usadas: list[str] = []
    fuentes_consultadas = list(resultado_busqueda.get("fuentes_consultadas_base") or [])

    for capa_data in (resultado_busqueda.get("resultados") or {}).values():
        parsed = (capa_data or {}).get("parsed") or {}
        for item in parsed.get("hallazgos", []) or []:
            url = (item or {}).get("fuente_url")
            if url:
                fuentes_usadas.append(url)

    usadas_unicas = sorted({u for u in fuentes_usadas if u})
    consultadas_unicas = sorted({c for c in fuentes_consultadas if c})

    descartadas = sorted(set(consultadas_unicas) - set(usadas_unicas))
    return {
        "consultadas": consultadas_unicas,
        "usadas": usadas_unicas,
        "descartadas": descartadas,
    }


def redactar_informe(resultado_busqueda: dict[str, Any]) -> dict[str, Any]:
    modelo = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
    max_tokens = int(os.getenv("MISTRAL_TOKENS", "4000"))

    rango_inicio = _parse_datetime(resultado_busqueda.get("rango_inicio", ""))
    rango_fin = _parse_datetime(resultado_busqueda.get("rango_fin", ""))

    rango_inicio_iso = rango_inicio.isoformat() if rango_inicio else resultado_busqueda.get("rango_inicio", "")
    rango_fin_iso = rango_fin.isoformat() if rango_fin else resultado_busqueda.get("rango_fin", "")

    hallazgos = _extraer_hallazgos(resultado_busqueda)
    min_por_seccion = _min_por_seccion()
    omitidas = _secciones_omitidas(hallazgos, min_por_seccion)
    contexto = _construir_contexto(resultado_busqueda)
    fuentes = _fuentes_desde_resultados(resultado_busqueda)
    inventarios = _extraer_inventarios(resultado_busqueda)

    if not contexto:
        texto = (
            "# INFORME POLÍTICO: VENEZUELA\n"
            f"Rango analizado (UTC): {rango_inicio_iso} a {rango_fin_iso}\n\n"
            "No se encontraron hallazgos verificables dentro del rango y los umbrales configurados.\n\n"
        )
        if omitidas:
            texto += "Secciones omitidas por bajo volumen: " + ", ".join(
                f"{o['seccion']} ({o['hallazgos']})" for o in omitidas
            )
            texto += "\n\n"
        texto += "Fuentes consultadas: " + ", ".join(fuentes.get("consultadas", []))
        html = f"<h1>Informe CENTINELA PRO</h1><pre>{texto}</pre>"
        return {
            "success": True,
            "informe_texto": texto,
            "informe_html": html,
            "metadata": {
                "modelo": modelo,
                "tokens": max_tokens,
                "rango_inicio": rango_inicio_iso,
                "rango_fin": rango_fin_iso,
                "min_hallazgos_seccion": min_por_seccion,
                "secciones_omitidas": omitidas,
                "pesos": {
                    "capa": CAPA_PESO,
                    "categoria": CATEGORIA_PESO,
                    "actor": ACTOR_PESO,
                },
            },
            "fuentes": fuentes,
        }

    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        texto = (
            "# INFORME POLÍTICO: VENEZUELA\n"
            f"Rango analizado (UTC): {rango_inicio_iso} a {rango_fin_iso}\n\n"
            "No se configuró MISTRAL_API_KEY. Se entrega resumen base con resultados de búsqueda.\n\n"
            f"{contexto}\n\n"
        )
        if omitidas:
            texto += "Secciones omitidas por bajo volumen: " + ", ".join(
                f"{o['seccion']} ({o['hallazgos']})" for o in omitidas
            )
            texto += "\n\n"
        texto += "Fuentes consultadas: " + ", ".join(fuentes.get("consultadas", []))
        html = f"<h1>Informe CENTINELA PRO</h1><pre>{texto}</pre>"
        return {
            "success": True,
            "informe_texto": texto,
            "informe_html": html,
            "metadata": {
                "modelo": modelo,
                "tokens": max_tokens,
                "rango_inicio": rango_inicio_iso,
                "rango_fin": rango_fin_iso,
                "min_hallazgos_seccion": min_por_seccion,
                "secciones_omitidas": omitidas,
                "pesos": {
                    "capa": CAPA_PESO,
                    "categoria": CATEGORIA_PESO,
                    "actor": ACTOR_PESO,
                },
            },
            "fuentes": fuentes,
        }

    inventario_texto = ""
    if inventarios:
        inventario_texto = "\nInventarios detectados:\n" + json.dumps(inventarios, ensure_ascii=False, indent=2)

    omitidas_texto = ""
    if omitidas:
        omitidas_texto = "\nSecciones omitidas por bajo volumen:\n" + "\n".join(
            f"- {o['seccion']}: {o['hallazgos']} hallazgos" for o in omitidas
        )

    prompt_usuario = (
        "Genera un informe con este formato EXACTO.\n"
        "1) TITULAR PRINCIPAL (1 línea)\n"
        "2) RESUMEN EJECUTIVO (120-140 palabras, empieza con: 'En esta entrega encontrarás...')\n"
        "3) TEMAS DOMINANTES (3-6 temas, cada uno con 2-3 líneas de contexto)\n"
        "4) ACTORES DESTACADOS (3-6, cada uno con 2-3 frases y objetivo/rol)\n"
        "5) HALLAZGOS POR SECCIÓN (solo secciones con contenido, en este orden fijo):\n"
        "   Nacional, Internacional / Geopolítica, Política, Economía, Energética / Hidrocarburos, DDHH, Seguridad, Otros\n"
        "   - Cada hallazgo debe tener 2-3 frases y cerrar con 'Link de verificación: URL'.\n"
        "6) INVENTARIO DE AUDITORÍA (si hay datos; si no, indica 'No disponible')\n"
        "7) CIERRE (2-3 bullets máximos)\n"
        "8) FUENTES (3 listas): Consultadas, Usadas, Descartadas\n\n"
        "Reglas estrictas:\n"
        "- Incluye SOLO hechos dentro del rango UTC exacto.\n"
        "- No uses Wikipedia ni fuentes no verificables.\n"
        "- Si una sección no tiene datos, omítela.\n"
        "- No inventes datos.\n"
        "- Estilo conciso y periodístico.\n\n"
        f"Rango UTC: {rango_inicio_iso} a {rango_fin_iso}\n\n"
        f"Material base (ordenado por relevancia, capa, categoría y actor):\n{contexto}\n\n"
        + omitidas_texto
        + "\n\nFuentes consultadas base (no necesariamente usadas):\n"
        + ", ".join(fuentes.get("consultadas", []))
        + inventario_texto
    )

    payload = {
        "model": modelo,
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": prompt_usuario},
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
        texto = _extraer_texto_respuesta(data)
        tokens_usados = (data.get("usage") or {}).get("total_tokens", 0)
        if not texto:
            texto = "No fue posible extraer texto de la respuesta del modelo."
        html = f"<h1>Informe CENTINELA PRO</h1><pre>{texto}</pre>"
        return {
            "success": True,
            "informe_texto": texto,
            "informe_html": html,
            "raw": data,
            "metadata": {
                "modelo": modelo,
                "tokens": max_tokens,
                "tokens_usados": tokens_usados,
                "rango_inicio": rango_inicio_iso,
                "rango_fin": rango_fin_iso,
                "min_hallazgos_seccion": min_por_seccion,
                "secciones_omitidas": omitidas,
                "pesos": {
                    "capa": CAPA_PESO,
                    "categoria": CATEGORIA_PESO,
                    "actor": ACTOR_PESO,
                },
            },
            "fuentes": fuentes,
        }
    except Exception as exc:  # pragma: no cover - manejo defensivo
        fallback = (
            "# INFORME POLÍTICO: VENEZUELA\n"
            "No se pudo obtener respuesta del modelo. Se entrega compilación de hallazgos.\n\n"
            f"{contexto}"
        )
        return {
            "success": False,
            "informe_texto": fallback,
            "informe_html": f"<h1>Informe CENTINELA PRO</h1><pre>{fallback}</pre>",
            "error": str(exc),
            "metadata": {
                "modelo": modelo,
                "tokens": max_tokens,
                "rango_inicio": rango_inicio_iso,
                "rango_fin": rango_fin_iso,
                "min_hallazgos_seccion": min_por_seccion,
                "secciones_omitidas": omitidas,
                "pesos": {
                    "capa": CAPA_PESO,
                    "categoria": CATEGORIA_PESO,
                    "actor": ACTOR_PESO,
                },
            },
            "fuentes": fuentes,
        }


if __name__ == "__main__":
    ejemplo = {
        "rango_inicio": "2026-01-01T00:00:00+00:00",
        "rango_fin": "2026-01-01T06:00:00+00:00",
        "resultados": {},
    }
    print(json.dumps(redactar_informe(ejemplo), ensure_ascii=False, indent=2))
