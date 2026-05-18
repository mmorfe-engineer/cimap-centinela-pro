from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import requests

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
CONFIG_PATH = os.getenv("CENTINELA_CONFIG_PATH", "config/monitor_noticias_multicapa_ve_v1_1.json")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _detectar_turno(ahora_utc: datetime) -> str:
    hora_vet = (ahora_utc - timedelta(hours=4)).hour
    return "12H" if hora_vet < 18 else "18H"


def _calcular_rango(ahora_utc: datetime, horas_atras: int | None) -> tuple[datetime, datetime, str]:
    turno = _detectar_turno(ahora_utc)
    if horas_atras is None:
        horas_atras = 12 if turno == "12H" else 6
    horas_atras = max(1, min(int(horas_atras), 72))
    inicio = ahora_utc - timedelta(hours=horas_atras)
    return inicio, ahora_utc, turno


def _ajustar_filtros_fecha(inicio: datetime, fin: datetime) -> tuple[str, str]:
    """
    Evita after:YYYY-MM-DD before:YYYY-MM-DD con la misma fecha.
    - Si inicio y fin caen el mismo día UTC, expande a 48h (día anterior + día siguiente).
    - before siempre es exclusivo, por eso sumamos 1 día.
    """
    after_date = inicio.date()
    before_date = fin.date()
    if after_date == before_date:
        after_date = after_date - timedelta(days=1)
    before_date = before_date + timedelta(days=1)
    return after_date.strftime("%Y-%m-%d"), before_date.strftime("%Y-%m-%d")


def _correlativo(fecha_utc: datetime, turno: str) -> str:
    return f"{fecha_utc.strftime('%Y%m%d')}-{turno}-{fecha_utc.strftime('%H%M%S')}"


def _cargar_config() -> dict[str, Any] | None:
    path = Path(CONFIG_PATH)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resumen_fuentes_config(config: dict[str, Any]) -> str:
    registry = config.get("source_registry") or []
    if not registry:
        return ""
    partes = []
    for fuente in registry:
        nombre = fuente.get("name") or fuente.get("domain")
        if not nombre:
            continue
        tipo = fuente.get("source_type") or ""
        partes.append(f"- {nombre} ({tipo})")
    return "\n".join(partes)


def _capas_desde_config(config: dict[str, Any], cuentas_monitoreadas: list[str]) -> dict[str, dict[str, Any]]:
    capas: dict[str, dict[str, Any]] = {}
    for layer in config.get("layers") or []:
        layer_id = layer.get("layer_id")
        if layer_id is None:
            continue
        name = layer.get("name") or f"layer_{layer_id}"
        mission = layer.get("mission") or ""
        submodules = layer.get("submodules") or []
        queries = []
        for sub in submodules:
            queries.extend(sub.get("query_templates") or [])
        queries.extend(layer.get("query_templates") or [])
        selection_rules = layer.get("selection_rules") or []
        descripcion = f"{name}. {mission}".strip()
        capas[f"capa_{layer_id}"] = {
            "descripcion": descripcion,
            "fuentes_consultadas": [name],
            "inventario": bool(cuentas_monitoreadas) and int(layer_id) == 2,
            "query_templates": queries,
            "selection_rules": selection_rules,
        }
    return capas


def _consulta_perplexity(prompt: str, timeout: int) -> dict[str, Any]:
    api_key = os.getenv("PERPLEXITY_API_KEY", "").strip()
    if not api_key:
        return {"success": False, "error": "PERPLEXITY_API_KEY no configurada", "texto": ""}

    payload = {
        "model": os.getenv("PERPLEXITY_MODEL", "sonar"),
        "messages": [
            {
                "role": "system",
                "content": "Eres un asistente de monitoreo. Devuelve hallazgos verificables con fuentes y fecha.",
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
    except Exception as exc:  # pragma: no cover - manejo defensivo
        return {"success": False, "error": str(exc), "texto": ""}


def _intentar_parsear_json(texto: str) -> dict[str, Any] | None:
    if not texto:
        return None
    try:
        return json.loads(texto)
    except Exception:
        return None


def _cuentas_monitoreadas() -> list[str]:
    raw = os.getenv("MONITORED_ACCOUNTS", "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


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
    except Exception as exc:  # pragma: no cover - defensivo
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
                resultados["archive_today"] = urljoin(response.url, match.group(1).strip().strip("\"'"))
        if "archive_today" not in resultados and "archive_today_error" not in resultados:
            resultados["archive_today_error"] = f"HTTP {response.status_code}"
    except Exception as exc:  # pragma: no cover - defensivo
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


def _anotar_capa(parsed: dict[str, Any] | None, capa: str, descripcion: str) -> None:
    if not parsed:
        return
    hallazgos = parsed.get("hallazgos")
    if not isinstance(hallazgos, list):
        return
    for item in hallazgos:
        if isinstance(item, dict):
            item.setdefault("capa", capa)
            item.setdefault("capa_descripcion", descripcion)


def _prompt_directrices(capa: dict[str, Any], config: dict[str, Any]) -> str:
    partes = []
    queries = capa.get("query_templates") or []
    if queries:
        partes.append("\nConsultas sugeridas:\n" + "\n".join(f"- {q}" for q in queries))
    rules = capa.get("selection_rules") or []
    if rules:
        partes.append("\nReglas de selección:\n" + "\n".join(f"- {r}" for r in rules))
    fuentes = _resumen_fuentes_config(config)
    if fuentes:
        partes.append("\nFuentes prioritarias (registro):\n" + fuentes)
    return "\n".join(partes)


def buscar_noticias(horas_atras: int | None = None) -> dict[str, Any]:
    ahora = _utc_now()
    inicio, fin, turno = _calcular_rango(ahora, horas_atras)
    correlativo = _correlativo(fin, turno)

    after, before = _ajustar_filtros_fecha(inicio, fin)
    timeout = int(os.getenv("PERPLEXITY_TIMEOUT", "30"))

    cuentas_monitoreadas = _cuentas_monitoreadas()
    inventario_instruccion = ""
    if cuentas_monitoreadas:
        inventario_instruccion = (
            "Incluye además un bloque inventario_auditoria con este formato exacto:\n"
            "{\n"
            "  \"total_cuentas\": N,\n"
            "  \"publico_directamente\": [\"@cuenta\", ...],\n"
            "  \"mencionada_sin_publicar\": [\"@cuenta\", ...],\n"
            "  \"sin_actividad\": [\"@cuenta\", ...],\n"
            "  \"corte_vet\": \"YYYY-MM-DD HH:MM VET\"\n"
            "}\n"
            f"Lista base de cuentas: {', '.join(cuentas_monitoreadas)}\n"
        )

    config = _cargar_config() or {}
    capas = _capas_desde_config(config, cuentas_monitoreadas)
    if not capas:
        capas = {
            "capa_1": {
                "descripcion": "X/Twitter y fuentes espejo (incluyendo fallback de visibilidad).",
                "fuentes_consultadas": ["X/Twitter", "Fuentes espejo"],
                "inventario": bool(cuentas_monitoreadas),
            },
            "capa_2": {
                "descripcion": "Redes sociales indirectas y portales indexables.",
                "fuentes_consultadas": ["Redes sociales indirectas", "Portales indexables"],
                "inventario": False,
            },
            "capa_3": {
                "descripcion": "Prensa regional y nacional de Venezuela.",
                "fuentes_consultadas": ["Prensa regional", "Prensa nacional"],
                "inventario": False,
            },
            "capa_4": {
                "descripcion": "Cobertura internacional y geopolítica (medios regionales y globales).",
                "fuentes_consultadas": ["Medios internacionales", "Geopolítica regional"],
                "inventario": False,
            },
            "capa_5": {
                "descripcion": "Energía e hidrocarburos (fuentes sectoriales y comunicados oficiales).",
                "fuentes_consultadas": ["Sector energético", "Comunicados oficiales"],
                "inventario": False,
            },
            "capa_6": {
                "descripcion": "ONG, organismos multilaterales y boletines especializados.",
                "fuentes_consultadas": ["ONG", "Organismos multilaterales", "Boletines especializados"],
                "inventario": False,
            },
        }

    resultados: dict[str, Any] = {}
    errores: list[str] = []

    rango_inicio_iso = inicio.isoformat()
    rango_fin_iso = fin.isoformat()

    for capa, detalle in capas.items():
        directrices = _prompt_directrices(detalle, config) if config else ""
        prompt = (
            "Monitorea eventos políticos relevantes de Venezuela.\n"
            f"Capa: {detalle['descripcion']}\n"
            f"Rango UTC exacto: {rango_inicio_iso} a {rango_fin_iso}.\n"
            f"Filtro seguro: after:{after} before:{before}.\n"
            "Incluye SOLO eventos dentro del rango UTC exacto.\n"
            "Devuelve JSON válido con esta forma exacta:\n"
            "{\n"
            "  \"hallazgos\": [\n"
            "    {\"fecha_hora_utc\": \"ISO\", \"titulo\": \"...\", \"resumen_1_frase\": \"...\", "
            "\"actor_principal\": \"...\", \"ubicacion\": \"...\", "
            "\"categoria\": \"nacional|internacional|geopolitica|politica|economia|ddhh|energia|hidrocarburos|seguridad|otros\", "
            "\"relevancia\": \"alta|media|baja\", \"relevancia_motivo\": \"...\", "
            "\"fuente_nombre\": \"...\", \"fuente_url\": \"https://...\"}\n"
            "  ],\n"
            "  \"fuentes_consultadas\": [\"...\"],\n"
            "  \"notas\": \"...\"\n"
            "}\n"
            "Si no hay hallazgos, devuelve hallazgos:[] y explica en notas.\n"
        )
        if detalle.get("inventario"):
            prompt += inventario_instruccion
        if directrices:
            prompt += "\nDirectrices adicionales:\n" + directrices + "\n"

        salida = _consulta_perplexity(prompt, timeout=timeout)
        parsed = _intentar_parsear_json(salida.get("texto", ""))
        _anotar_capa(parsed, capa, detalle.get("descripcion", ""))
        _archivar_hallazgos(parsed)
        salida["parsed"] = parsed
        salida["fuentes_consultadas_base"] = detalle.get("fuentes_consultadas", [])
        resultados[capa] = salida
        if not salida.get("success"):
            errores.append(f"{capa}: {salida.get('error', 'Error desconocido')}")

    fuentes_consultadas_base = sorted(
        {fuente for detalle in capas.values() for fuente in detalle.get("fuentes_consultadas", [])}
    )

    return {
        "success": len(errores) < len(capas),
        "turno": turno,
        "correlativo": correlativo,
        "rango_inicio": rango_inicio_iso,
        "rango_fin": rango_fin_iso,
        "after": after,
        "before": before,
        "resultados": resultados,
        "errores": errores,
        "fuentes_consultadas_base": fuentes_consultadas_base,
    }


if __name__ == "__main__":
    horas_raw = os.getenv("HORAS_ATRAS", "").strip()
    horas = int(horas_raw) if horas_raw.isdigit() else None
    print(json.dumps(buscar_noticias(horas), ensure_ascii=False, indent=2))
