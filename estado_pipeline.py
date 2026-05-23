"""
CENTINELA PRO — Estado Pipeline (Fase D)
=========================================

Gestiona la persistencia de snapshots entre cortes para habilitar:
- Sección "Novedades vs. corte anterior" en el informe
- Detección de silencios prolongados (actores sin actividad en ≥2 cortes)
- Tracking de cambio de estado general (escalada/desescalada)
- Persistencia de alertas A4 (qué sigue activo, qué se resolvió)

ARQUITECTURA:
- Snapshots se almacenan en una rama dedicada 'estado' del repo
  (mismo patrón que gh-pages — git worktree + commit + push)
- Cada snapshot: `estado/<correlativo>.json`
- Índice rotativo: `estado/historial.json` lista los últimos 14 cortes
- Cargar: clona rama estado a worktree, lee snapshot más reciente
- Guardar: clona, escribe, actualiza historial, commit, push, limpia
- Tolera ausencia de rama estado en primera ejecución (la crea)

FLUJO TYPICAL (orquestado por monitor.py):
1. buscar_noticias() → busqueda dict
2. cargar_snapshot_anterior() + cargar_historial_reciente()  ← LECTURA
3. detectar_novedades(busqueda, snapshot_previo, historial) → novedades dict
4. redactar_informe(busqueda, novedades=novedades)
5. guardar_snapshot(busqueda, redaccion, correlativo)  ← ESCRITURA
6. entregar_informe(redaccion, correlativo)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

# =============================================================================
# Configuración
# =============================================================================

CONFIG_PATH = os.getenv(
    "CENTINELA_CONFIG_PATH",
    "config/monitor_noticias_multicapa_ve_v1_1.json",
)
ACTORES_PATH = os.getenv(
    "CENTINELA_ACTORES_PATH",
    "config/actores.json",
)

# Rama dedicada para snapshots (paralela a gh-pages)
ESTADO_BRANCH = "estado"

# Cuántos snapshots conservar en el historial rotativo
HISTORIAL_MAX = 14

# Cuántos cortes consecutivos sin actividad para alertar silencio prolongado
SILENCIO_UMBRAL_CORTES = 2

# Estado general → ranking numérico para detectar escalada/desescalada
ESTADO_RANK = {
    "RUTINARIO": 0,
    "NORMAL": 1,
    "MEDIO": 2,
    "ALTO": 3,
    "CRITICO": 4,
}


# =============================================================================
# Utilidades
# =============================================================================


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _cargar_json_archivo(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _indexar_actores() -> dict[str, dict[str, Any]]:
    """Carga catálogo de actores e indexa por código."""
    data = _cargar_json_archivo(Path(ACTORES_PATH)) or {}
    if not isinstance(data, dict):
        return {}
    index: dict[str, dict[str, Any]] = {}
    for actor in data.get("actores", []) or []:
        codigo = actor.get("codigo")
        if codigo:
            index[codigo] = actor
    return index


# =============================================================================
# Hashing de hallazgos para detección de persistencia
# =============================================================================


def _normalizar_titulo(titulo: str) -> str:
    """
    Normaliza un título para que dos versiones ligeramente distintas del mismo
    evento produzcan el mismo hash:
    - Lowercase
    - Remueve acentos
    - Remueve puntuación
    - Colapsa espacios
    - Toma primeros 60 caracteres
    """
    if not titulo:
        return ""
    s = titulo.lower().strip()
    # Eliminar acentos básicos
    acentos = str.maketrans("áéíóúñü", "aeiounu")
    s = s.translate(acentos)
    # Eliminar puntuación
    s = re.sub(r"[^\w\s]", "", s)
    # Colapsar espacios
    s = re.sub(r"\s+", " ", s).strip()
    return s[:60]


def _hash_hallazgo(actor: str, titulo: str) -> str:
    """Hash estable basado en actor + titulo normalizado."""
    base = f"{actor.strip().upper()}::{_normalizar_titulo(titulo)}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


# =============================================================================
# Git worktree — acceso a la rama 'estado'
# =============================================================================


def _crear_worktree_estado() -> tuple[TemporaryDirectory | None, Path | None, bool]:
    """
    Crea un worktree para la rama 'estado'.

    Retorna (tmp_dir_handle, path_al_worktree, branch_existia_remotamente).
    Si falla, retorna (None, None, False).

    El llamador debe asegurar la limpieza con _limpiar_worktree() o _commit_y_limpiar_worktree().
    """
    repo_root = Path(__file__).resolve().parent
    tmp_dir = TemporaryDirectory(prefix="estado-")
    worktree = Path(tmp_dir.name) / "site"

    try:
        # ¿Existe la rama 'estado' remotamente?
        remote_check = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", ESTADO_BRANCH],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        branch_existe = bool(remote_check.stdout.strip())

        if branch_existe:
            subprocess.run(
                ["git", "fetch", "origin", f"{ESTADO_BRANCH}:refs/heads/{ESTADO_BRANCH}"],
                cwd=repo_root,
                check=False,
                capture_output=True,
            )
            subprocess.run(
                ["git", "worktree", "add", str(worktree), ESTADO_BRANCH],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            subprocess.run(
                ["git", "worktree", "add", "--orphan", "-b", ESTADO_BRANCH, str(worktree)],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

        # Identidad git en el worktree
        subprocess.run(
            ["git", "config", "user.name", "github-actions[bot]"],
            cwd=worktree, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            cwd=worktree, check=True,
        )

        # Asegurar carpeta estado/ existe
        (worktree / "estado").mkdir(parents=True, exist_ok=True)

        return tmp_dir, worktree, branch_existe
    except Exception as exc:
        # Cleanup en caso de fallo parcial
        tmp_dir.cleanup()
        print(f"[estado_pipeline] ERROR creando worktree: {exc}")
        return None, None, False


def _limpiar_worktree(tmp_dir: TemporaryDirectory, worktree: Path) -> None:
    repo_root = Path(__file__).resolve().parent
    subprocess.run(
        ["git", "worktree", "remove", str(worktree), "--force"],
        cwd=repo_root, check=False,
    )
    subprocess.run(["git", "worktree", "prune"], cwd=repo_root, check=False)
    tmp_dir.cleanup()


def _commit_y_push_estado(worktree: Path, mensaje: str) -> bool:
    """Commit y push de cambios en el worktree de estado."""
    try:
        subprocess.run(["git", "add", "estado"], cwd=worktree, check=True)
        commit = subprocess.run(
            ["git", "commit", "-m", mensaje],
            cwd=worktree, check=False, capture_output=True, text=True,
        )
        if commit.returncode == 0:
            subprocess.run(["git", "push", "origin", ESTADO_BRANCH], cwd=worktree, check=True)
            return True
        # commit returncode != 0 puede significar "nothing to commit", lo cual es OK
        return True
    except Exception as exc:
        print(f"[estado_pipeline] ERROR commit/push estado: {exc}")
        return False


# =============================================================================
# Lectura: snapshots y historial
# =============================================================================


def cargar_snapshot_anterior() -> dict[str, Any] | None:
    """
    Carga el snapshot más reciente desde la rama 'estado'.
    Retorna None si no existe o si la rama no existe (primer corte ever).
    """
    tmp_dir, worktree, branch_existe = _crear_worktree_estado()
    if not worktree:
        return None

    try:
        if not branch_existe:
            return None  # primer corte ever
        historial_file = worktree / "estado" / "historial.json"
        historial = _cargar_json_archivo(historial_file) or []
        if not historial or not isinstance(historial, list):
            return None
        # El historial está ordenado del más reciente al más antiguo
        ultimo_correlativo = historial[0].get("correlativo") if isinstance(historial[0], dict) else None
        if not ultimo_correlativo:
            return None
        snapshot_path = worktree / "estado" / f"{ultimo_correlativo}.json"
        snapshot = _cargar_json_archivo(snapshot_path)
        if isinstance(snapshot, dict):
            return snapshot
        return None
    finally:
        if tmp_dir and worktree:
            _limpiar_worktree(tmp_dir, worktree)


def cargar_historial_reciente(n: int = HISTORIAL_MAX) -> list[dict[str, Any]]:
    """
    Carga los últimos N snapshots desde la rama 'estado'.
    Ordenados del más reciente al más antiguo. Retorna lista vacía si no hay.
    """
    tmp_dir, worktree, branch_existe = _crear_worktree_estado()
    if not worktree:
        return []

    try:
        if not branch_existe:
            return []
        historial_file = worktree / "estado" / "historial.json"
        historial = _cargar_json_archivo(historial_file) or []
        if not isinstance(historial, list):
            return []
        snapshots = []
        for entrada in historial[:n]:
            if not isinstance(entrada, dict):
                continue
            correlativo = entrada.get("correlativo")
            if not correlativo:
                continue
            snapshot_path = worktree / "estado" / f"{correlativo}.json"
            snapshot = _cargar_json_archivo(snapshot_path)
            if isinstance(snapshot, dict):
                snapshots.append(snapshot)
        return snapshots
    finally:
        if tmp_dir and worktree:
            _limpiar_worktree(tmp_dir, worktree)


# =============================================================================
# Construcción del snapshot del corte actual
# =============================================================================


def _extraer_todos_hallazgos(busqueda: dict[str, Any]) -> list[dict[str, Any]]:
    """Lista plana deduplicada de hallazgos desde la salida del buscador."""
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
            todos.append(item)
    return todos


def construir_snapshot_actual(
    busqueda: dict[str, Any],
    redaccion: dict[str, Any],
    correlativo: str,
) -> dict[str, Any]:
    """
    Construye el snapshot estructurado del corte actual.
    Esto es lo que se guarda en estado/<correlativo>.json.
    """
    hallazgos = _extraer_todos_hallazgos(busqueda)
    meta_redaccion = redaccion.get("metadata") or {}

    actores_activos: dict[str, dict[str, Any]] = {}
    hashes_hallazgos: list[dict[str, Any]] = []
    alertas_a4: list[dict[str, Any]] = []

    for h in hallazgos:
        actor_ref = str(h.get("actor_principal") or "").strip()
        titulo = str(h.get("titulo") or "").strip()
        alert_level = str(h.get("alert_level") or "A2")
        url = h.get("fuente_url") or ""

        # Hash del hallazgo
        h_hash = _hash_hallazgo(actor_ref, titulo)
        hashes_hallazgos.append({
            "hash": h_hash,
            "actor": actor_ref,
            "alert_level": alert_level,
            "titulo_corto": titulo[:100],
            "fuente_url": url,
        })

        # Actor activo
        if actor_ref:
            if actor_ref not in actores_activos:
                actores_activos[actor_ref] = {
                    "n_hallazgos": 0,
                    "max_alert_level": "A0",
                    "alert_levels": [],
                    "ultimo_titulo": "",
                    "ultima_fuente_url": "",
                }
            actor_data = actores_activos[actor_ref]
            actor_data["n_hallazgos"] += 1
            actor_data["alert_levels"].append(alert_level)
            actor_data["ultimo_titulo"] = titulo[:100]
            actor_data["ultima_fuente_url"] = url
            # Max alert level
            if ESTADO_RANK.get(alert_level.replace("A", "RANK_"), 0) > ESTADO_RANK.get(actor_data["max_alert_level"].replace("A", "RANK_"), 0):
                actor_data["max_alert_level"] = alert_level

        # Recolectar A4
        if alert_level == "A4":
            alertas_a4.append({
                "hash": h_hash,
                "actor": actor_ref,
                "titulo": titulo[:120],
                "fuente_url": url,
            })

    # max_alert_level corregido (la línea anterior tiene un bug de comparación)
    # Re-calcular limpio:
    alert_rank = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4}
    for actor_ref, actor_data in actores_activos.items():
        max_lvl = "A0"
        for lvl in actor_data["alert_levels"]:
            if alert_rank.get(lvl, 0) > alert_rank.get(max_lvl, 0):
                max_lvl = lvl
        actor_data["max_alert_level"] = max_lvl

    snapshot = {
        "version": "1.0",
        "correlativo": correlativo,
        "turno": busqueda.get("turno", "cierre"),
        "timestamp_utc": _utc_now_iso(),
        "rango_inicio": busqueda.get("rango_inicio", ""),
        "rango_fin": busqueda.get("rango_fin", ""),
        "estado_general": meta_redaccion.get("estado_general", "NORMAL"),
        "contadores": meta_redaccion.get("contadores", {}),
        "actores_activos": actores_activos,
        "actores_en_silencio": meta_redaccion.get("actores_en_silencio", []),
        "hallazgos_hashes": hashes_hallazgos,
        "alertas_a4": alertas_a4,
        "contradicciones_n": int(meta_redaccion.get("hallazgos_contradictorios") or 0),
        "alertas_sa_n": int(meta_redaccion.get("alertas_tempranas_sa") or 0),
    }
    return snapshot


# =============================================================================
# Detección de novedades
# =============================================================================


def _calcular_silencios_prolongados(
    historial: list[dict[str, Any]],
    actores_index: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """
    Para cada actor de alta prioridad, cuenta cortes consecutivos sin actividad
    desde el más reciente hacia atrás.

    Retorna dict {codigo_actor: n_cortes_sin_actividad} para los que superan umbral.
    """
    if not historial:
        return {}

    silencios = {}
    actores_alta = [
        a for a in actores_index.values()
        if a.get("prioridad_monitoreo") == "alta"
    ]

    for actor in actores_alta:
        codigo = actor.get("codigo")
        if not codigo:
            continue
        cortes_sin = 0
        for snapshot in historial:  # más reciente primero
            activos = snapshot.get("actores_activos") or {}
            if codigo in activos:
                break  # encontró actividad
            cortes_sin += 1
        if cortes_sin >= SILENCIO_UMBRAL_CORTES:
            silencios[codigo] = cortes_sin
    return silencios


def detectar_novedades(
    busqueda_actual: dict[str, Any],
    snapshot_anterior: dict[str, Any] | None,
    historial: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Compara hallazgos actuales contra el snapshot anterior y produce dict
    estructurado para que redactor.py renderice la sección "Novedades".
    """
    actores_index = _indexar_actores()
    historial = historial or []

    # === Caso primer corte ===
    if not snapshot_anterior:
        return {
            "es_primer_corte": True,
            "mensaje": "Primer corte registrado en el sistema. No hay comparación disponible aún; los próximos cortes mostrarán novedades.",
            "hallazgos_nuevos": [],
            "hallazgos_persistentes": [],
            "actores_que_aparecieron": [],
            "actores_que_callaron": [],
            "silencios_prolongados": [],
            "cambio_estado": None,
            "alertas_a4_nuevas": [],
            "alertas_a4_persistentes": [],
            "alertas_a4_resueltas": [],
            "ref_corte_anterior": None,
        }

    # === Caso normal: comparación ===

    # 1. Hallazgos del corte actual
    hallazgos_actuales = _extraer_todos_hallazgos(busqueda_actual)
    hashes_actuales: dict[str, dict[str, Any]] = {}
    actores_actuales: set[str] = set()

    for h in hallazgos_actuales:
        actor_ref = str(h.get("actor_principal") or "").strip()
        titulo = str(h.get("titulo") or "").strip()
        h_hash = _hash_hallazgo(actor_ref, titulo)
        hashes_actuales[h_hash] = h
        if actor_ref:
            actores_actuales.add(actor_ref)

    # 2. Hallazgos del corte anterior (del snapshot)
    hashes_previos_lista = snapshot_anterior.get("hallazgos_hashes") or []
    hashes_previos: dict[str, dict[str, Any]] = {
        item["hash"]: item for item in hashes_previos_lista
        if isinstance(item, dict) and item.get("hash")
    }
    actores_previos: set[str] = set(
        (snapshot_anterior.get("actores_activos") or {}).keys()
    )

    # 3. Diff de hashes
    set_actuales = set(hashes_actuales.keys())
    set_previos = set(hashes_previos.keys())

    hashes_nuevos = set_actuales - set_previos
    hashes_resueltos = set_previos - set_actuales
    hashes_persistentes = set_actuales & set_previos

    # 4. Construir listas estructuradas
    hallazgos_nuevos = []
    for h_hash in hashes_nuevos:
        h = hashes_actuales[h_hash]
        hallazgos_nuevos.append({
            "hash": h_hash,
            "actor": str(h.get("actor_principal") or ""),
            "alert_level": str(h.get("alert_level") or "A2"),
            "titulo": str(h.get("titulo") or "")[:120],
            "fuente_url": h.get("fuente_url", ""),
        })

    hallazgos_persistentes = []
    for h_hash in hashes_persistentes:
        h = hashes_actuales[h_hash]
        hallazgos_persistentes.append({
            "hash": h_hash,
            "actor": str(h.get("actor_principal") or ""),
            "alert_level": str(h.get("alert_level") or "A2"),
            "titulo": str(h.get("titulo") or "")[:120],
        })

    # 5. Actores que aparecieron / callaron
    actores_que_aparecieron = sorted(actores_actuales - actores_previos)
    actores_que_callaron = sorted(actores_previos - actores_actuales)

    # 6. Silencios prolongados (requiere historial)
    silencios_dict = _calcular_silencios_prolongados(historial, actores_index)
    silencios_prolongados = []
    for codigo, cortes in sorted(silencios_dict.items(), key=lambda x: -x[1]):
        actor_info = actores_index.get(codigo, {})
        silencios_prolongados.append({
            "codigo": codigo,
            "nombre": actor_info.get("nombre", codigo),
            "cargo": actor_info.get("cargo", ""),
            "cortes_sin_actividad": cortes,
        })

    # 7. Cambio de estado
    estado_actual = "NORMAL"
    if hallazgos_actuales:
        ranks = [
            ESTADO_RANK.get(s, 1)
            for s in [snapshot_anterior.get("estado_general", "NORMAL")]
        ]
    # Calcular estado actual desde los hallazgos
    contadores_actuales = {
        "A4": sum(1 for h in hallazgos_actuales if h.get("alert_level") == "A4"),
        "A3": sum(1 for h in hallazgos_actuales if h.get("alert_level") == "A3"),
        "A2": sum(1 for h in hallazgos_actuales if h.get("alert_level") == "A2"),
    }
    if contadores_actuales["A4"] >= 1:
        estado_actual = "CRITICO"
    elif contadores_actuales["A3"] >= 2:
        estado_actual = "ALTO"
    elif contadores_actuales["A3"] >= 1 or contadores_actuales["A2"] >= 3:
        estado_actual = "MEDIO"
    elif hallazgos_actuales:
        estado_actual = "NORMAL"
    else:
        estado_actual = "RUTINARIO"

    estado_previo = snapshot_anterior.get("estado_general", "NORMAL")
    rank_actual = ESTADO_RANK.get(estado_actual, 1)
    rank_previo = ESTADO_RANK.get(estado_previo, 1)
    if rank_actual > rank_previo:
        direccion = "escalada"
    elif rank_actual < rank_previo:
        direccion = "desescalada"
    else:
        direccion = "igual"
    cambio_estado = {
        "previo": estado_previo,
        "actual": estado_actual,
        "direccion": direccion,
    }

    # 8. Alertas A4: nuevas / persistentes / resueltas
    a4_previas_hashes = {
        item["hash"] for item in (snapshot_anterior.get("alertas_a4") or [])
        if isinstance(item, dict) and item.get("hash")
    }
    a4_actuales_hashes = {
        h_hash for h_hash, h in hashes_actuales.items()
        if h.get("alert_level") == "A4"
    }

    a4_nuevas_set = a4_actuales_hashes - a4_previas_hashes
    a4_persistentes_set = a4_actuales_hashes & a4_previas_hashes
    a4_resueltas_set = a4_previas_hashes - a4_actuales_hashes

    alertas_a4_nuevas = [
        {
            "hash": h_hash,
            "actor": str(hashes_actuales[h_hash].get("actor_principal") or ""),
            "titulo": str(hashes_actuales[h_hash].get("titulo") or "")[:120],
            "fuente_url": hashes_actuales[h_hash].get("fuente_url", ""),
        }
        for h_hash in a4_nuevas_set
    ]
    alertas_a4_persistentes = [
        {
            "hash": h_hash,
            "actor": str(hashes_actuales[h_hash].get("actor_principal") or ""),
            "titulo": str(hashes_actuales[h_hash].get("titulo") or "")[:120],
        }
        for h_hash in a4_persistentes_set
    ]
    # Para resueltas usamos data del snapshot previo
    a4_previas_index = {
        item["hash"]: item for item in (snapshot_anterior.get("alertas_a4") or [])
        if isinstance(item, dict) and item.get("hash")
    }
    alertas_a4_resueltas = [
        {
            "hash": h_hash,
            "actor": a4_previas_index[h_hash].get("actor", ""),
            "titulo": a4_previas_index[h_hash].get("titulo", "")[:120],
        }
        for h_hash in a4_resueltas_set
        if h_hash in a4_previas_index
    ]

    return {
        "es_primer_corte": False,
        "ref_corte_anterior": {
            "correlativo": snapshot_anterior.get("correlativo", ""),
            "turno": snapshot_anterior.get("turno", ""),
            "timestamp_utc": snapshot_anterior.get("timestamp_utc", ""),
        },
        "hallazgos_nuevos": hallazgos_nuevos,
        "hallazgos_persistentes": hallazgos_persistentes,
        "actores_que_aparecieron": actores_que_aparecieron,
        "actores_que_callaron": actores_que_callaron,
        "silencios_prolongados": silencios_prolongados,
        "cambio_estado": cambio_estado,
        "alertas_a4_nuevas": alertas_a4_nuevas,
        "alertas_a4_persistentes": alertas_a4_persistentes,
        "alertas_a4_resueltas": alertas_a4_resueltas,
    }


# =============================================================================
# Escritura: guardar snapshot del corte actual
# =============================================================================


def _actualizar_historial(
    worktree: Path,
    correlativo: str,
    snapshot: dict[str, Any],
) -> None:
    """
    Mantiene `estado/historial.json` como una lista del más reciente al más antiguo,
    con máximo HISTORIAL_MAX entradas. Borra archivos snapshot fuera del horizon.
    """
    historial_file = worktree / "estado" / "historial.json"
    historial = _cargar_json_archivo(historial_file) or []
    if not isinstance(historial, list):
        historial = []

    nueva_entrada = {
        "correlativo": correlativo,
        "turno": snapshot.get("turno", ""),
        "timestamp_utc": snapshot.get("timestamp_utc", ""),
        "estado_general": snapshot.get("estado_general", "NORMAL"),
        "contadores": snapshot.get("contadores", {}),
    }

    # Evitar duplicados por correlativo (si re-ejecutas el mismo corte)
    historial = [e for e in historial if isinstance(e, dict) and e.get("correlativo") != correlativo]
    historial.insert(0, nueva_entrada)

    # Recortar a HISTORIAL_MAX
    if len(historial) > HISTORIAL_MAX:
        historial_pruned = historial[:HISTORIAL_MAX]
        # Borrar snapshots viejos del worktree
        correlativos_vivos = {e.get("correlativo") for e in historial_pruned if isinstance(e, dict)}
        for old_snapshot in (worktree / "estado").glob("*.json"):
            if old_snapshot.name == "historial.json":
                continue
            if old_snapshot.stem not in correlativos_vivos:
                try:
                    old_snapshot.unlink()
                except Exception:
                    pass
        historial = historial_pruned

    historial_file.write_text(
        json.dumps(historial, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def guardar_snapshot(
    busqueda: dict[str, Any],
    redaccion: dict[str, Any],
    correlativo: str,
) -> dict[str, Any]:
    """
    Construye el snapshot del corte actual y lo persiste en la rama estado.
    Retorna dict con success + detalle.
    """
    snapshot = construir_snapshot_actual(busqueda, redaccion, correlativo)

    tmp_dir, worktree, _ = _crear_worktree_estado()
    if not worktree:
        return {
            "success": False,
            "detalle": "No se pudo crear worktree para rama estado",
            "snapshot_local": snapshot,
        }

    try:
        snapshot_file = worktree / "estado" / f"{correlativo}.json"
        snapshot_file.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _actualizar_historial(worktree, correlativo, snapshot)

        # Commit y push
        push_ok = _commit_y_push_estado(
            worktree,
            f"chore(estado): snapshot {correlativo}",
        )
        return {
            "success": push_ok,
            "detalle": "Snapshot guardado en rama estado" if push_ok else "Snapshot creado pero push falló",
            "snapshot_local": snapshot,
        }
    except Exception as exc:
        return {
            "success": False,
            "detalle": f"Error al guardar snapshot: {exc}",
            "snapshot_local": snapshot,
        }
    finally:
        if tmp_dir and worktree:
            _limpiar_worktree(tmp_dir, worktree)


# =============================================================================
# Entry point para debugging
# =============================================================================


if __name__ == "__main__":
    print("[estado_pipeline] Test de carga del snapshot anterior...")
    s = cargar_snapshot_anterior()
    if s:
        print(f"  Snapshot anterior encontrado: {s.get('correlativo')} ({s.get('turno')})")
        print(f"  Estado: {s.get('estado_general')} | Hallazgos: {len(s.get('hallazgos_hashes', []))}")
    else:
        print("  Sin snapshot anterior (primer corte).")
    print("[estado_pipeline] Test de carga del historial reciente...")
    h = cargar_historial_reciente()
    print(f"  Snapshots en historial: {len(h)}")
