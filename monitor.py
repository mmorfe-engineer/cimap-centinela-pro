"""
CENTINELA PRO — Monitor / Orquestador (Fase D)
===============================================

Orquesta el pipeline completo:
  1) BÚSQUEDA       buscador.buscar_noticias()
  2) ESTADO (lectura)  estado_pipeline.cargar_snapshot_anterior() + cargar_historial_reciente()
  3) NOVEDADES      estado_pipeline.detectar_novedades()
  4) REDACCIÓN      redactor.redactar_informe(busqueda, novedades=...)
  5) ENTREGA        entrega.entregar_informe()
  6) ESTADO (escritura)  estado_pipeline.guardar_snapshot()

El componente de estado (Fase D) es NO CRÍTICO: si la lectura o escritura del
snapshot falla (p.ej. rama 'estado' inaccesible), el pipeline continúa y produce
el informe igualmente — solo que sin la sección "Novedades vs. corte anterior".
Esto garantiza que un fallo en la memoria entre cortes nunca tumba la entrega.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from buscador import buscar_noticias
from entrega import entregar_informe
from redactor import redactar_informe

# Fase D — import defensivo: si el módulo no existe, el pipeline sigue sin estado.
try:
    import estado_pipeline
    _ESTADO_DISPONIBLE = True
except ImportError:
    estado_pipeline = None  # type: ignore
    _ESTADO_DISPONIBLE = False


def _parse_horas_atras(value: str | None) -> int | None:
    """
    Acepta None/""/"6"/"6.0"/"  6  " -> int o None.
    Rechaza negativos, cero y texto no numérico -> None.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        n = int(float(s))
    except ValueError:
        return None
    return n if n > 0 else None


def _estado_habilitado() -> bool:
    """
    El estado se activa si:
    - El módulo estado_pipeline está disponible, y
    - La env var CENTINELA_ESTADO != "0" (permite desactivarlo manualmente).
    """
    if not _ESTADO_DISPONIBLE:
        return False
    return os.getenv("CENTINELA_ESTADO", "1").strip().lower() not in {"0", "false", "no"}


def _utc_stamp_for_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ejecutar_orquestacion(horas_atras: int | None) -> dict[str, Any]:
    """
    Orquesta el pipeline completo con memoria entre cortes (Fase D).
    """
    # --- 1) BÚSQUEDA ---
    resultado_busqueda = buscar_noticias(horas_atras=horas_atras)
    correlativo = resultado_busqueda.get("correlativo", "sin-correlativo")

    # --- 2) ESTADO (lectura) — no crítico ---
    snapshot_anterior: dict[str, Any] | None = None
    historial: list[dict[str, Any]] = []
    estado_lectura_detalle = "desactivado"
    if _estado_habilitado():
        try:
            snapshot_anterior = estado_pipeline.cargar_snapshot_anterior()
            historial = estado_pipeline.cargar_historial_reciente()
            estado_lectura_detalle = (
                f"snapshot_anterior={'sí' if snapshot_anterior else 'no'}, "
                f"historial={len(historial)}"
            )
        except Exception as exc:
            estado_lectura_detalle = f"error lectura: {exc}"

    # --- 3) NOVEDADES — no crítico ---
    novedades: dict[str, Any] | None = None
    if _estado_habilitado():
        try:
            novedades = estado_pipeline.detectar_novedades(
                resultado_busqueda, snapshot_anterior, historial
            )
        except Exception as exc:
            novedades = None
            estado_lectura_detalle += f" | error novedades: {exc}"

    # --- 4) REDACCIÓN (recibe novedades si las hay) ---
    resultado_redaccion = redactar_informe(resultado_busqueda, novedades=novedades)

    # --- 5) ENTREGA ---
    resultado_entrega = entregar_informe(resultado_redaccion, correlativo)

    # --- 6) ESTADO (escritura) — no crítico ---
    estado_escritura: dict[str, Any] = {"success": False, "detalle": "desactivado"}
    if _estado_habilitado():
        try:
            estado_escritura = estado_pipeline.guardar_snapshot(
                resultado_busqueda, resultado_redaccion, correlativo
            )
        except Exception as exc:
            estado_escritura = {"success": False, "detalle": f"error escritura: {exc}"}

    bus_ok = bool(resultado_busqueda.get("success"))
    red_ok = bool(resultado_redaccion.get("success"))
    ent_ok = bool(resultado_entrega.get("success"))

    salida: dict[str, Any] = {
        # El éxito operativo NO depende del estado (Fase D es no crítica)
        "success": bus_ok and red_ok and ent_ok,
        "metadata": {
            "utc": datetime.now(timezone.utc).isoformat(),
            "horas_atras": horas_atras,
            "correlativo": correlativo,
            "turno": resultado_busqueda.get("turno"),
            "profile": resultado_busqueda.get("profile"),
            "estado_habilitado": _estado_habilitado(),
            "estado_lectura": estado_lectura_detalle,
        },
        "busqueda": resultado_busqueda,
        "redaccion": resultado_redaccion,
        "entrega": resultado_entrega,
        "estado": {
            "lectura": estado_lectura_detalle,
            "novedades_generadas": bool(novedades and not novedades.get("es_primer_corte")),
            "es_primer_corte": bool(novedades and novedades.get("es_primer_corte")),
            "escritura": estado_escritura,
        },
        "canales": (resultado_entrega.get("resultados", {}) or {}),
    }
    return salida


def _persist_outputs(salida: dict[str, Any]) -> dict[str, str]:
    """Escribe salidas/ultimo_resultado.json y un snapshot con timestamp."""
    out_dir = Path("salidas")
    out_dir.mkdir(exist_ok=True)

    latest = out_dir / "ultimo_resultado.json"
    _write_json(latest, salida)

    correlativo = (salida.get("metadata") or {}).get("correlativo") or "sin-correlativo"
    stamp = _utc_stamp_for_filename()
    snap = out_dir / f"{stamp}_{correlativo}.json"
    _write_json(snap, salida)

    return {"latest": str(latest), "snapshot": str(snap)}


def _exit_code(salida: dict[str, Any]) -> int:
    """
    Exit codes para CI:
      0 = todo OK
      2 = falló búsqueda
      3 = falló redacción
      4 = falló entrega
      1 = fallo genérico
    El estado (Fase D) NO afecta el exit code (es no crítico).
    """
    if salida.get("success"):
        return 0
    bus_ok = bool((salida.get("busqueda") or {}).get("success"))
    red_ok = bool((salida.get("redaccion") or {}).get("success"))
    ent_ok = bool((salida.get("entrega") or {}).get("success"))
    if not bus_ok:
        return 2
    if not red_ok:
        return 3
    if not ent_ok:
        return 4
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="CENTINELA PRO - Orquestador (Monitor) - Fase D")
    parser.add_argument(
        "horas_atras",
        nargs="?",
        default=None,
        help="Horas hacia atrás (opcional). Si no se pasa, se usa HORAS_ATRAS env si existe.",
    )
    args = parser.parse_args()

    horas_atras = _parse_horas_atras(args.horas_atras)
    if horas_atras is None:
        horas_atras = _parse_horas_atras(os.getenv("HORAS_ATRAS"))

    salida = ejecutar_orquestacion(horas_atras=horas_atras)
    paths = _persist_outputs(salida)

    meta = salida.get("metadata") or {}
    estado = salida.get("estado") or {}
    print(
        f"[centinela] correlativo={meta.get('correlativo')} "
        f"turno={meta.get('turno')} profile={meta.get('profile')} "
        f"horas_atras={horas_atras!r}"
    )
    print(
        f"[centinela] estado: habilitado={meta.get('estado_habilitado')} "
        f"novedades={estado.get('novedades_generadas')} "
        f"primer_corte={estado.get('es_primer_corte')} "
        f"escritura_ok={(estado.get('escritura') or {}).get('success')}"
    )
    print(f"[centinela] outputs: latest={paths['latest']} snapshot={paths['snapshot']}")

    return _exit_code(salida)


if __name__ == "__main__":
    raise SystemExit(main())
