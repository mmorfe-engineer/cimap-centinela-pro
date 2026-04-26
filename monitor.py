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


def _parse_horas_atras(value: str | None) -> int | None:
    """
    Acepta:
      - None / ""  -> None
      - "6"        -> 6
      - "6.0"      -> 6
      - "  6  "    -> 6
    Rechaza:
      - negativos / 0 -> None
      - texto no numérico -> None
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


def _utc_stamp_for_filename() -> str:
    # estable para nombres de archivo
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ejecutar_orquestacion(horas_atras: int | None) -> dict[str, Any]:
    """
    Orquesta:
      1) búsqueda (Perplexity)
      2) redacción (Mistral)
      3) entrega (Telegram/Slack/Discord/Gmail/Pages)
    """
    resultado_busqueda = buscar_noticias(horas_atras=horas_atras)
    resultado_redaccion = redactar_informe(resultado_busqueda)

    correlativo = resultado_busqueda.get("correlativo", "sin-correlativo")
    resultado_entrega = entregar_informe(resultado_redaccion, correlativo)

    bus_ok = bool(resultado_busqueda.get("success"))
    red_ok = bool(resultado_redaccion.get("success"))
    ent_ok = bool(resultado_entrega.get("success"))

    salida: dict[str, Any] = {
        "success": bus_ok and red_ok and ent_ok,
        "metadata": {
            "utc": datetime.now(timezone.utc).isoformat(),
            "horas_atras": horas_atras,
            "correlativo": correlativo,
        },
        "busqueda": resultado_busqueda,
        "redaccion": resultado_redaccion,
        "entrega": resultado_entrega,
        # contrato estable para dashboards/lectores:
        "canales": (resultado_entrega.get("resultados", {}) or {}),
    }
    return salida


def _persist_outputs(salida: dict[str, Any]) -> dict[str, str]:
    """
    Escribe:
      - salidas/ultimo_resultado.json
      - salidas/<stamp>_<correlativo>.json
    Retorna paths creados (para logs).
    """
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
    parser = argparse.ArgumentParser(description="CENTINELA PRO - Orquestador (Monitor)")
    parser.add_argument(
        "horas_atras",
        nargs="?",
        default=None,
        help="Horas hacia atrás (opcional). Si no se pasa, se usa HORAS_ATRAS env si existe.",
    )
    args = parser.parse_args()

    # Prioridad: argumento CLI > variable de entorno
    horas_atras = _parse_horas_atras(args.horas_atras)
    if horas_atras is None:
        horas_atras = _parse_horas_atras(os.getenv("HORAS_ATRAS"))

    salida = ejecutar_orquestacion(horas_atras=horas_atras)
    paths = _persist_outputs(salida)

    # Logs “humanos���
    print(f"[centinela] horas_atras={horas_atras!r} correlativo={(salida.get('metadata') or {}).get('correlativo')}")
    print(f"[centinela] outputs: latest={paths['latest']} snapshot={paths['snapshot']}")
    print(json.dumps(salida, ensure_ascii=False, indent=2))

    return _exit_code(salida)


if __name__ == "__main__":
    raise SystemExit(main())
