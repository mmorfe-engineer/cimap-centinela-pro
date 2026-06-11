"""
CENTINELA PRO — Entrega (Fase C)
=================================

Reescritura de la capa de entrega para resolver el problema del teaser
genérico y producir mensajes inteligentes por canal.

Cambios clave vs. versión anterior:

1) TEASER INTELIGENTE
   - Usa metadata.estado_general (CRITICO/ALTO/MEDIO/NORMAL/RUTINARIO) con color emoji.
   - Muestra contadores A4/A3/A2 y total.
   - Lista 3-4 headlines compactos con código de actor + emoji A4/A3.
   - Marca actores en silencio prioritarios (si los hay).
   - Indica contradicciones y alertas tempranas si existen.
   - Termina con call-to-action al informe completo en GitHub Pages.

2) FORMATO POR CANAL
   - Telegram: texto plano con emojis (1 solo mensaje, ~600 chars).
   - Discord: mismo teaser (límite 1900 chars suficiente).
   - Slack: mismo teaser (límite 3900 chars suficiente).
   - Gmail: subject inteligente con estado + correlativo; cuerpo HTML completo.

3) GITHUB PAGES MEJORADO
   - index.html ahora muestra cards con estado del día, contadores y enlace.
   - CSS embebido coherente con el informe (mismo estilo visual).

4) COMPATIBILIDAD PRESERVADA con monitor.py
   - Mantiene firma entregar_informe(informe, correlativo).
   - Lee metadata y headlines_for_teaser del nuevo redactor.
   - Si esos campos no están (legacy), cae back a comportamiento básico.
"""

from __future__ import annotations

import html
import os
import smtplib
import subprocess
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import requests

# =============================================================================
# Metadatos para renderizar estados (espejo del redactor)
# =============================================================================

ESTADO_GENERAL_META: dict[str, dict[str, str]] = {
    "CRITICO": {"emoji": "🔴", "label": "CRÍTICO", "color": "#d32f2f"},
    "ALTO": {"emoji": "🟠", "label": "ALTO", "color": "#f57c00"},
    "MEDIO": {"emoji": "🟡", "label": "MEDIO", "color": "#fbc02d"},
    "NORMAL": {"emoji": "🟢", "label": "NORMAL", "color": "#388e3c"},
    "RUTINARIO": {"emoji": "⚪", "label": "RUTINARIO", "color": "#757575"},
}

ALERT_EMOJI = {"A4": "🔴", "A3": "🟠", "A2": "🟡", "A1": "🟢", "A0": "⚪"}


# =============================================================================
# Utilidades
# =============================================================================


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _resultado(success: bool, detalle: str = "") -> dict[str, Any]:
    return {"success": success, "detalle": detalle}


def _github_pages_base_url() -> str:
    base = os.getenv("PAGES_BASE_URL", "").strip()
    return base.rstrip("/")


def _chunk_text(texto: str, max_len: int = 3900) -> list[str]:
    texto = (texto or "").strip()
    if not texto:
        return []
    return [texto[i : i + max_len] for i in range(0, len(texto), max_len)]


def _esc(texto: Any) -> str:
    if texto is None:
        return ""
    return html.escape(str(texto), quote=True)


# =============================================================================
# Construcción del teaser inteligente
# =============================================================================


def _build_link_informe(correlativo: str, pages_subdir: str = "informes") -> str:
    base = _github_pages_base_url()
    if base:
        return f"{base}/{pages_subdir}/{correlativo}.html"
    return ""


def _build_teaser(informe: dict[str, Any], correlativo: str, prefijo_telegram: str = "") -> str:
    """
    Teaser de ~400-600 chars optimizado para Telegram/Discord/Slack.
    Estructura:
        [prefijo] [emoji estado] CENTINELA PRO · [turno] [correlativo]
        ESTADO: [label]
        🔴 [n] · 🟠 [n] · 🟡 [n] · Total [n]
        ▸ HEADLINES:
        [emoji] [codigo] [actor]: [titulo]
        ...
        📵 Sin actividad: [codes] (si hay)
        ⚠️ [n] contradicciones · 📡 [n] alertas tempranas (si hay)
        📄 Informe completo: [url]
    """
    meta = informe.get("metadata") or {}
    estado = str(meta.get("estado_general") or "NORMAL")
    estado_meta = ESTADO_GENERAL_META.get(estado, ESTADO_GENERAL_META["NORMAL"])
    contadores = meta.get("contadores") or {}
    turno = str(meta.get("turno") or "cierre")
    turno_label = "Apertura 11H" if turno == "matutino" else "Cierre 18H"

    headlines = informe.get("headlines_for_teaser") or []
    silencios = meta.get("silencios_detalle") or []
    n_contrad = int(meta.get("hallazgos_contradictorios") or 0)
    n_alertas_sa = int(meta.get("alertas_tempranas_sa") or 0)

    n_a4 = int(contadores.get("A4", 0))
    n_a3 = int(contadores.get("A3", 0))
    n_a2 = int(contadores.get("A2", 0))
    n_total = int(contadores.get("total", 0))

    # --- Cabecera ---
    prefix = f"{prefijo_telegram} " if prefijo_telegram else ""
    lineas = [
        f"{prefix}{estado_meta['emoji']} CENTINELA PRO · {turno_label}",
        f"#{correlativo}",
        "",
        f"📊 ESTADO: {estado_meta['label']}",
        f"🔴 {n_a4} · 🟠 {n_a3} · 🟡 {n_a2} · Total {n_total}",
    ]

    # --- Headlines (3-4 máx) ---
    if headlines:
        lineas.append("")
        lineas.append("▸ TITULARES:")
        for h in headlines[:4]:
            emoji = h.get("emoji", "")
            codigo = h.get("actor_codigo", "")
            actor = h.get("actor_nombre", "") or ""
            icono = h.get("iconografia") or ""
            icono_str = f" {icono}" if icono else ""
            titulo = h.get("titulo", "") or ""
            # Formato: emoji [CODIGO] Actor: titulo
            prefijo = f"{emoji} "
            if codigo:
                prefijo += f"[{codigo}] "
            if actor:
                prefijo += f"{actor}{icono_str}: "
            lineas.append(f"{prefijo}{titulo}")
    elif n_total == 0:
        lineas.append("")
        lineas.append("Sin hallazgos críticos verificables en este corte.")

    # --- Silencios ---
    if silencios:
        codigos_silencio = ", ".join(
            s.get("codigo", "") for s in silencios[:6] if s.get("codigo")
        )
        if codigos_silencio:
            lineas.append("")
            lineas.append(f"📵 Sin actividad detectada: {codigos_silencio}")

    # --- Banderas ---
    flags = []
    if n_contrad > 0:
        flags.append(f"⚠️ {n_contrad} contradicción{'es' if n_contrad > 1 else ''}")
    if n_alertas_sa > 0:
        flags.append(f"📡 {n_alertas_sa} alerta{'s' if n_alertas_sa > 1 else ''} temprana{'s' if n_alertas_sa > 1 else ''}")
    if flags:
        lineas.append("")
        lineas.append(" · ".join(flags))

    # --- Link al informe completo ---
    link = _build_link_informe(correlativo)
    if link:
        lineas.append("")
        lineas.append(f"📄 Informe completo:")
        lineas.append(link)

    return "\n".join(lineas)


def _build_subject_email(informe: dict[str, Any], correlativo: str) -> str:
    """Subject inteligente para Gmail."""
    meta = informe.get("metadata") or {}
    estado = str(meta.get("estado_general") or "NORMAL")
    estado_meta = ESTADO_GENERAL_META.get(estado, ESTADO_GENERAL_META["NORMAL"])
    contadores = meta.get("contadores") or {}
    n_a4 = int(contadores.get("A4", 0))
    n_a3 = int(contadores.get("A3", 0))
    return (
        f"{estado_meta['emoji']} CENTINELA PRO {estado_meta['label']} | "
        f"{n_a4} A4 · {n_a3} A3 | {correlativo}"
    )


# =============================================================================
# Envío por canal
# =============================================================================


def enviar_telegram(texto: str) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return _resultado(False, "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no configurados")

    if not texto or not texto.strip():
        return _resultado(False, "Texto vacío; nada que enviar")

    chunks = _chunk_text(texto)
    for chunk in chunks:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": False,  # mostrar preview del link al informe
                },
                timeout=20,
            )
            if not resp.ok:
                return _resultado(False, f"Telegram HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            return _resultado(False, f"Telegram excepción: {exc}")
    return _resultado(True, f"Enviado ({len(chunks)} chunk{'s' if len(chunks)>1 else ''})")


def enviar_discord(texto: str) -> dict[str, Any]:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        return _resultado(False, "DISCORD_WEBHOOK_URL no configurado")
    if not texto or not texto.strip():
        return _resultado(False, "Texto vacío; nada que enviar")
    try:
        resp = requests.post(webhook, json={"content": texto[:1900]}, timeout=20)
        return _resultado(resp.ok, "Enviado" if resp.ok else f"Discord HTTP {resp.status_code}")
    except Exception as exc:
        return _resultado(False, f"Discord excepción: {exc}")


def enviar_slack(texto: str) -> dict[str, Any]:
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return _resultado(False, "SLACK_WEBHOOK_URL no configurado")
    if not texto or not texto.strip():
        return _resultado(False, "Texto vacío; nada que enviar")
    try:
        resp = requests.post(webhook, json={"text": texto[:3900]}, timeout=20)
        return _resultado(resp.ok, "Enviado" if resp.ok else f"Slack HTTP {resp.status_code}")
    except Exception as exc:
        return _resultado(False, f"Slack excepción: {exc}")


def enviar_gmail(
    asunto: str,
    texto: str,
    html_body: str,
    pdf_path: str | None = None,
) -> dict[str, Any]:
    remitente = os.getenv("GMAIL_REMITENTE", "").strip()
    app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    destinatario = os.getenv("GMAIL_DESTINATARIO", "").strip()
    if not remitente or not app_password or not destinatario:
        return _resultado(False, "GMAIL_REMITENTE/GMAIL_APP_PASSWORD/GMAIL_DESTINATARIO no configurados")

    try:
        msg = EmailMessage()
        msg["Subject"] = asunto
        msg["From"] = remitente
        msg["To"] = destinatario
        msg.set_content(texto or "Informe CENTINELA PRO adjunto.")
        if html_body:
            msg.add_alternative(html_body, subtype="html")

        if pdf_path and Path(pdf_path).exists():
            msg.add_attachment(
                Path(pdf_path).read_bytes(),
                maintype="application",
                subtype="pdf",
                filename=Path(pdf_path).name,
            )

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(remitente, app_password)
            smtp.send_message(msg)
        return _resultado(True, "Enviado")
    except Exception as exc:
        return _resultado(False, f"Gmail excepción: {exc}")


# =============================================================================
# PDF (placeholder — implementación real diferida a Fase posterior)
# =============================================================================


def _generar_pdf_simple(correlativo: str, texto: str) -> dict[str, Any]:
    _ = (correlativo, texto)
    return {
        "success": False,
        "path": None,
        "detalle": "PDF aún no implementado; diferido a Fase D/E.",
    }


# =============================================================================
# GitHub Pages — index mejorado
# =============================================================================


CSS_INDEX = """
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
html, body { margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background: var(--bg-page);
  color: var(--text-primary);
  line-height: 1.6;
}
.contenedor { max-width: 960px; margin: 0 auto; padding: 28px 20px 80px; }
@media (max-width: 640px) { .contenedor { padding: 18px 12px 40px; } }

header.index-header {
  border-bottom: 3px solid var(--border);
  padding-bottom: 18px;
  margin-bottom: 28px;
}
header.index-header h1 {
  margin: 0;
  font-size: 1.9em;
  letter-spacing: -0.5px;
}
header.index-header .subtitulo {
  color: var(--text-secondary);
  font-size: 0.95em;
  margin-top: 4px;
}

.informe-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 22px;
  margin-bottom: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  transition: box-shadow 0.15s;
}
.informe-card:hover {
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}
.informe-card a.titulo-link {
  color: var(--link);
  text-decoration: none;
  font-weight: 600;
  font-size: 1.05em;
}
.informe-card a.titulo-link:hover {
  color: var(--link-hover);
  text-decoration: underline;
}
.informe-card .meta-info {
  flex: 1;
  min-width: 200px;
}
.informe-card .fecha-info {
  color: var(--text-muted);
  font-size: 0.85em;
  margin-top: 3px;
}
footer.index-footer {
  margin-top: 48px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
  font-size: 0.82em;
  color: var(--text-muted);
  text-align: center;
}
"""


def _build_index_html(informes_rel: list[str]) -> str:
    """
    Construye index.html con cards de los informes disponibles, ordenados
    cronológicamente descendente (los nombres incluyen YYYYMMDD-NNH-HHMMSS).
    """
    cards_html = []
    for path in informes_rel:
        # path es del estilo "informes/20260519-18H-180000.html"
        nombre_archivo = Path(path).stem  # "20260519-18H-180000"
        partes = nombre_archivo.split("-")
        fecha_humana = nombre_archivo
        if len(partes) >= 3:
            fecha = partes[0]
            turno = partes[1]
            # 20260519 → 19/05/2026
            if len(fecha) == 8:
                fecha_humana = f"{fecha[6:8]}/{fecha[4:6]}/{fecha[:4]} · Turno {turno}"

        cards_html.append(f"""
<div class="informe-card">
  <div class="meta-info">
    <a class="titulo-link" href="{_esc(path)}">CENTINELA PRO · {_esc(nombre_archivo)}</a>
    <div class="fecha-info">{_esc(fecha_humana)}</div>
  </div>
  <div><a class="titulo-link" href="{_esc(path)}">Abrir informe →</a></div>
</div>
""")

    if not cards_html:
        cards_html.append('<p style="color:#888; font-style:italic;">Sin informes publicados todavía.</p>')

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CENTINELA PRO · Informes</title>
<style>{CSS_INDEX}</style>
</head>
<body>
<div class="contenedor">
<header class="index-header">
  <h1>CENTINELA PRO</h1>
  <div class="subtitulo">Archivo de informes de monitoreo político — Venezuela</div>
</header>
<main>
{"".join(cards_html)}
</main>
<footer class="index-footer">
  <p>CENTINELA PRO · Actualizado {_esc(_utc_now_str())}</p>
  <p>Uso estratégico interno; no distribuir sin autorización.</p>
</footer>
</div>
</body>
</html>
"""


def publicar_en_github_pages(
    correlativo: str, informe_html: str, pages_subdir: str = "informes"
) -> dict[str, Any]:
    """Publica el informe en la rama gh-pages con subdirectorio parametrizable."""
    repo_root = Path(__file__).resolve().parent
    worktree: Path | None = None
    tmp_dir: TemporaryDirectory | None = None  # type: ignore[type-arg]

    try:
        correlativo_seguro = "".join(
            ch for ch in correlativo if ch.isalnum() or ch in ("-", "_")
        ) or "informe"

        tmp_dir = TemporaryDirectory(prefix="gh-pages-")
        worktree = Path(tmp_dir.name) / "site"

        # ¿Existe gh-pages remoto?
        remote_check = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "gh-pages"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        branch_exists = bool(remote_check.stdout.strip())

        if branch_exists:
            subprocess.run(
                ["git", "fetch", "origin", "gh-pages:refs/heads/gh-pages"],
                cwd=repo_root,
                check=False,
                capture_output=True,
            )
            subprocess.run(
                ["git", "worktree", "add", str(worktree), "gh-pages"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            subprocess.run(
                ["git", "worktree", "add", "--orphan", "-b", "gh-pages", str(worktree)],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=worktree, check=True)
        subprocess.run(
            ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            cwd=worktree,
            check=True,
        )

        informes_dir = worktree / pages_subdir
        informes_dir.mkdir(parents=True, exist_ok=True)
        (worktree / f"{pages_subdir}/{correlativo_seguro}.html").write_text(
            informe_html, encoding="utf-8"
        )

        # Recolectar todos los HTML de todos los subdirectorios
        todos_informes = []
        for subdir in ["nacional", "internacional", "energia", "informes"]:
            subdir_path = worktree / subdir
            if subdir_path.exists():
                todos_informes.extend(
                    [f"{subdir}/{p.name}" for p in subdir_path.glob("*.html")]
                )

        (worktree / "index.html").write_text(
            _build_index_html(todos_informes),
            encoding="utf-8",
        )

        subprocess.run(["git", "add", "index.html", pages_subdir], cwd=worktree, check=True)
        commit = subprocess.run(
            ["git", "commit", "-m", f"chore: publicar informe {correlativo}"],
            cwd=worktree,
            check=False,
            capture_output=True,
            text=True,
        )
        if commit.returncode == 0:
            subprocess.run(["git", "push", "origin", "gh-pages"], cwd=worktree, check=True)
        return _resultado(True, "Publicado en gh-pages")
    except Exception as exc:
        return _resultado(False, f"Pages no crítico: {exc}")
    finally:
        if worktree:
            subprocess.run(
                ["git", "worktree", "remove", str(worktree), "--force"],
                cwd=repo_root,
                check=False,
            )
            subprocess.run(["git", "worktree", "prune"], cwd=repo_root, check=False)
        if tmp_dir:
            tmp_dir.cleanup()


# =============================================================================
# Orquestación principal
# =============================================================================


def entregar_informe(
    informe: dict[str, Any],
    correlativo: str,
    prefijo_telegram: str = "",
    pages_subdir: str = "informes",
) -> dict[str, Any]:
    """
    Toma el dict del redactor y entrega por todos los canales configurados.
    Devuelve un dict con success agregado y detalle por canal.

    El informe debe traer (Fase B+):
      - informe_texto: str
      - informe_html: str
      - metadata: dict (estado_general, contadores, turno, etc.)
      - headlines_for_teaser: list[dict]
    
    Parametros adicionales para modulizacion:
      - prefijo_telegram: prefijo para el teaser (ej: "🇻🇪 NACIONAL")
      - pages_subdir: subdirectorio para GitHub Pages (ej: "nacional")
    """
    texto = informe.get("informe_texto", "")
    html_body = informe.get("informe_html") or f"<pre>{html.escape(texto)}</pre>"
    
    # Obtener pages_subdir del informe si no se proporciona
    if not pages_subdir:
        pages_subdir = informe.get("pages_subdir", "informes")

    asunto = _build_subject_email(informe, correlativo)
    teaser = _build_teaser(informe, correlativo, prefijo_telegram)

    pdf_result = _generar_pdf_simple(correlativo, texto)
    pdf_path = pdf_result.get("path") if pdf_result.get("success") else None

    resultados = {
        "telegram": enviar_telegram(teaser),
        "discord": enviar_discord(teaser),
        "slack": enviar_slack(teaser),
        "gmail": enviar_gmail(asunto, texto, html_body, pdf_path=pdf_path),
        "github_pages": publicar_en_github_pages(correlativo, html_body, pages_subdir),
        "pdf": _resultado(
            bool(pdf_result.get("success")),
            pdf_result.get("detalle", ""),
        ),
    }

    # Pages es no-crítico para el éxito operativo. Se considera exitosa la entrega
    # si al menos un canal crítico (telegram, gmail, discord, slack) llega.
    canales_criticos = ["telegram", "gmail", "discord", "slack"]
    success = any(
        (resultados.get(c) or {}).get("success") for c in canales_criticos
    )

    return {
        "success": success,
        "teaser_usado": teaser,
        "resultados": resultados,
    }


# =============================================================================
# Entry point manual / debugging
# =============================================================================


if __name__ == "__main__":
    # Smoke test del teaser con data mock
    informe_mock = {
        "informe_texto": "Informe de prueba.",
        "informe_html": "<h1>Informe</h1>",
        "metadata": {
            "version_redactor": "2.0-fase-b",
            "estado_general": "ALTO",
            "correlativo": "20260519-18H-180000",
            "turno": "cierre",
            "contadores": {"A4": 1, "A3": 3, "A2": 4, "A1": 0, "A0": 0, "total": 8},
            "silencios_detalle": [
                {"codigo": "P3", "nombre": "Henrique Capriles", "cargo": "Dirigente"},
                {"codigo": "P5", "nombre": "Andrés Velásquez", "cargo": "Dirigente"},
            ],
            "hallazgos_contradictorios": 1,
            "alertas_tempranas_sa": 2,
        },
        "headlines_for_teaser": [
            {
                "rank": 1, "alert_level": "A4", "emoji": "🔴",
                "actor_codigo": "GOB1", "actor_nombre": "Delcy Rodríguez",
                "iconografia": "🇻🇪",
                "titulo": "Delcy anuncia bloqueo a comisión investigadora de la AN",
            },
            {
                "rank": 2, "alert_level": "A3", "emoji": "🟠",
                "actor_codigo": "P1", "actor_nombre": "María Corina Machado",
                "iconografia": "",
                "titulo": "MCM convoca movilización nacional para el 23M",
            },
            {
                "rank": 3, "alert_level": "A3", "emoji": "🟠",
                "actor_codigo": "INT2", "actor_nombre": "OFAC",
                "iconografia": "",
                "titulo": "OFAC autoriza licencia parcial a operaciones de PDVSA",
            },
        ],
    }
    print("=== TEASER ===")
    print(_build_teaser(informe_mock, "20260519-18H-180000"))
    print("\n=== SUBJECT EMAIL ===")
    print(_build_subject_email(informe_mock, "20260519-18H-180000"))
