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
    "Internacional",
    "Política",
    "Economía",
    "DDHH",
    "Energía",
    "Seguridad",
    "Otros",
]


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


def _extraer_hallazgos(resultado_busqueda: dict[str, Any]) -> list[dict[str, Any]]:
    hallazgos: list[dict[str, Any]] = []
    for capa_data in (resultado_busqueda.get("resultados") or {}).values():
        parsed = (capa_data or {}).get("parsed") or {}
        for item in parsed.get("hallazgos", []) or []:
            if isinstance(item, dict):
                hallazgos.append(item)
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
        "internacional": "Internacional",
        "politica": "Política",
        "política": "Política",
        "economia": "Economía",
        "economía": "Economía",
        "ddhh": "DDHH",
        "energia": "Energía",
        "energía": "Energía",
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


def _construir_contexto(resultado_busqueda: dict[str, Any]) -> str:
    hallazgos = _extraer_hallazgos(resultado_busqueda)
    if not hallazgos:
        return ""

    por_seccion = _agrupar_por_seccion(hallazgos)
    partes: list[str] = []
    for seccion in SECCIONES:
        items = por_seccion.get(seccion) or []
        if not items:
            continue
        partes.append(f"## {seccion}")
        for item in items:
            partes.append(
                f"- Fecha UTC: {item.get('fecha_hora_utc','')} | {item.get('titulo','')}\n"
                f"  Resumen: {item.get('resumen_1_frase','')}\n"
                f"  Actor: {item.get('actor_principal','')} | Ubicación: {item.get('ubicacion','')}\n"
                f"  Relevancia: {item.get('relevancia','')} | Motivo: {item.get('relevancia_motivo','')}\n"
                f"  Fuente: {item.get('fuente_nombre','')} | {item.get('fuente_url','')}"
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

    contexto = _construir_contexto(resultado_busqueda)
    fuentes = _fuentes_desde_resultados(resultado_busqueda)
    inventarios = _extraer_inventarios(resultado_busqueda)

    if not contexto:
        texto = (
            "# INFORME POLÍTICO: VENEZUELA\n"
            f"Rango analizado (UTC): {rango_inicio_iso} a {rango_fin_iso}\n\n"
            "No se encontraron hallazgos verificables dentro del rango.\n\n"
            "Fuentes consultadas: " + ", ".join(fuentes.get("consultadas", []))
        )
        html = f"<h1>Informe CENTINELA PRO</h1><pre>{texto}</pre>"
        return {
            "success": True,
            "informe_texto": texto,
            "informe_html": html,
            "metadata": {"modelo": modelo, "tokens": max_tokens, "rango_inicio": rango_inicio_iso, "rango_fin": rango_fin_iso},
            "fuentes": fuentes,
        }

    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        texto = (
            "# INFORME POLÍTICO: VENEZUELA\n"
            f"Rango analizado (UTC): {rango_inicio_iso} a {rango_fin_iso}\n\n"
            "No se configuró MISTRAL_API_KEY. Se entrega resumen base con resultados de búsqueda.\n\n"
            f"{contexto}\n\n"
            "Fuentes consultadas: " + ", ".join(fuentes.get("consultadas", []))
        )
        html = f"<h1>Informe CENTINELA PRO</h1><pre>{texto}</pre>"
        return {
            "success": True,
            "informe_texto": texto,
            "informe_html": html,
            "metadata": {"modelo": modelo, "tokens": max_tokens, "rango_inicio": rango_inicio_iso, "rango_fin": rango_fin_iso},
            "fuentes": fuentes,
        }

    inventario_texto = ""
    if inventarios:
        inventario_texto = "\nInventarios detectados:\n" + json.dumps(inventarios, ensure_ascii=False, indent=2)

    prompt_usuario = (
        "Genera un informe con este formato EXACTO.\n"
        "1) TITULAR PRINCIPAL (1 línea)\n"
        "2) RESUMEN EJECUTIVO (120-140 palabras, empieza con: 'En esta entrega encontrarás...')\n"
        "3) TEMAS DOMINANTES (3-6 temas, cada uno con 2-3 líneas de contexto)\n"
        "4) ACTORES DESTACADOS (3-6, cada uno con 2-3 frases y objetivo/rol)\n"
        "5) HALLAZGOS POR SECCIÓN (solo secciones con contenido, en este orden fijo):\n"
        "   Nacional, Internacional, Política, Economía, DDHH, Energía, Seguridad, Otros\n"
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
        f"Material base:\n{contexto}\n\n"
        "Fuentes consultadas base (no necesariamente usadas):\n"
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
            "metadata": {"modelo": modelo, "tokens": max_tokens, "rango_inicio": rango_inicio_iso, "rango_fin": rango_fin_iso},
            "fuentes": fuentes,
        }


if __name__ == "__main__":
    ejemplo = {
        "rango_inicio": "2026-01-01T00:00:00+00:00",
        "rango_fin": "2026-01-01T06:00:00+00:00",
        "resultados": {},
    }
    print(json.dumps(redactar_informe(ejemplo), ensure_ascii=False, indent=2))
