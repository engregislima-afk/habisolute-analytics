# app.py — Habisolute Analytics (corrigido + melhorias dinâmicas + fix verificação 3d)

import io, re, json, base64, tempfile, zipfile, hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import streamlit as st
import pandas as pd
import pdfplumber
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

# PDF (ReportLab)
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    Image as RLImage, PageBreak
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.platypus import KeepTogether, HRFlowable

# ===== Rodapé e numeração do PDF =====
FOOTER_TEXT = (
    "Estes resultados referem-se exclusivamente às amostras ensaiadas. "
    "Este documento poderá ser reproduzido somente na íntegra. "
    "Resultados apresentados sem considerar a incerteza de medição +- 0,80Mpa."
)
FOOTER_BRAND_TEXT = "Sistema Desenvolvido por IA e pela Habisolute Engenharia"
HABISOLUTE_SITE_URL = "https://www.habisoluteengenharia.com.br/"


def _qr_area_cliente_flowables(styles):
    """Bloco discreto exibido no encerramento de todos os PDFs."""
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle

    qr = QrCodeWidget(HABISOLUTE_SITE_URL)
    bounds = qr.getBounds()
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    qr_size = 46
    drawing = Drawing(qr_size, qr_size, transform=[qr_size / width, 0, 0, qr_size / height, 0, 0])
    drawing.add(qr)

    title_style = ParagraphStyle(
        "qr_area_cliente_title",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=10.5,
        textColor=colors.HexColor("#374151"),
        alignment=TA_LEFT,
    )
    text_style = ParagraphStyle(
        "qr_area_cliente_text",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#6B7280"),
        alignment=TA_LEFT,
    )

    text = [
        Paragraph("Acesse a Área do Cliente", title_style),
        Paragraph(
            "Aponte a câmera do celular para o QR Code e acesse o site da Habisolute.",
            text_style,
        ),
        Paragraph("www.habisoluteengenharia.com.br", text_style),
    ]
    text_table = Table([[item] for item in text], colWidths=[250])
    text_table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    block = Table([[drawing, text_table]], colWidths=[58, 260], hAlign="RIGHT")
    block.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor("#D1D5DB")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
    ]))

    return [
        Spacer(1, 12),
        HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#D1D5DB"), spaceBefore=0, spaceAfter=7),
        KeepTogether([block]),
    ]

class NumberedCanvas(pdfcanvas.Canvas):
    ORANGE = colors.HexColor("#c6c9cf")
    BLACK  = colors.black

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_fixed_bars_and_footer(total_pages)
            super().showPage()
        super().save()

    def _wrap_footer(self, text, font_name="Helvetica", font_size=7, max_width=None):
        if max_width is None:
            max_width = self._pagesize[0] - 36 - 120
        words = text.split()
        lines, line = [], ""
        for w in words:
            test = (line + " " + w).strip()
            if self.stringWidth(test, font_name, font_size) <= max_width:
                line = test
            else:
                if line:
                    lines.append(line)
                line = w
        if line:
            lines.append(line)
        return lines

    def _draw_fixed_bars_and_footer(self, total_pages: int):
        w, h = self._pagesize
        # Cabeçalho
        self.setFillColor(self.ORANGE); self.rect(0, h - 10, w, 6, stroke=0, fill=1)
        self.setFillColor(self.BLACK);   self.rect(0, h - 16, w, 2, stroke=0, fill=1)
        # Rodapé
        self.setFillColor(self.BLACK);   self.rect(0, 8, w, 2, stroke=0, fill=1)
        self.setFillColor(self.ORANGE);  self.rect(0, 12, w, 6, stroke=0, fill=1)
        # Textos
        y0 = 44
        self.setFillColor(colors.black); self.setFont("Helvetica", 7)
        lines = self._wrap_footer(FOOTER_TEXT, "Helvetica", 7, w - 36 - 100)
        for i, ln in enumerate(lines):
            y = y0 + i * 8; self.drawString(18, y, ln)
        self.setFont("Helvetica-Oblique", 8)
        self.drawCentredString(w / 2.0, y0 - 8, FOOTER_BRAND_TEXT)
        self.setFont("Helvetica", 8)
        self.drawRightString(w - 18, y0 - 18, f"Página {self._pageNumber} de {total_pages}")

# =============================================================================
# Configuração básica
# =============================================================================
st.set_page_config(page_title="Habisolute — Relatórios", layout="wide")

PREFS_DIR = Path.home() / ".habisolute"; PREFS_DIR.mkdir(parents=True, exist_ok=True)
PREFS_PATH = PREFS_DIR / "prefs.json"; USERS_DB = PREFS_DIR / "users.json"
AUDIT_LOG = PREFS_DIR / "audit.jsonl"

def _now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def log_event(action: str, meta: Dict[str, Any] | None = None, level: str = "INFO"):
    try:
        rec = {
            "ts": _now_iso(),
            "user": st.session_state.get("username") or "anon",
            "level": level,
            "action": action,
            "meta": meta or {},
        }
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

def read_audit_df() -> pd.DataFrame:
    if not AUDIT_LOG.exists():
        return pd.DataFrame(columns=["ts","user","level","action","meta"])
    rows = []
    with AUDIT_LOG.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                rows.append({
                    "ts": rec.get("ts"),
                    "user": rec.get("user"),
                    "level": rec.get("level"),
                    "action": rec.get("action"),
                    "meta": json.dumps(rec.get("meta") or {}, ensure_ascii=False),
                })
            except Exception:
                continue
    df = pd.DataFrame(rows, columns=["ts","user","level","action","meta"])
    if not df.empty:
        df = df.sort_values("ts", ascending=False, kind="stable").reset_index(drop=True)
    return df

# ----- prefs util -----
def _save_all_prefs(data: Dict[str, Any]) -> None:
    tmp = PREFS_DIR / "prefs.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(PREFS_PATH)

def _load_all_prefs() -> Dict[str, Any]:
    try:
        if PREFS_PATH.exists():
            return json.loads(PREFS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}

def load_user_prefs(key: str = "default") -> Dict[str, Any]:
    return _load_all_prefs().get(key, {})

def save_user_prefs(prefs: Dict[str, Any], key: str = "default") -> None:
    data = _load_all_prefs(); data[key] = prefs; _save_all_prefs(data)

# ===== Estado =====
s = st.session_state
# Acesso direto: autenticação removida.
# Mantemos um usuário interno fixo apenas para auditoria e permissões do sistema.
s["logged_in"] = True
s["username"] = "Habisolute"
s["is_admin"] = True
s["must_change"] = False
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Escuro moderno"))
s.setdefault("brand", load_user_prefs().get("brand", "Laranja"))
s.setdefault("uploader_key", 0); s.setdefault("OUTLIER_SIGMA", 3.0)
s.setdefault("TOL_MP", 1.0); s.setdefault("BATCH_MODE", False); s.setdefault("_prev_batch", s["BATCH_MODE"])
s.setdefault("last_sel_rels", [])
s.setdefault("last_date_range", None)
# novos campos de cabeçalho de relatório
s.setdefault("rt_responsavel", "")
s.setdefault("rt_cliente", "")
s.setdefault("rt_cidade", "")
s.setdefault("rt_material", "Concreto")
# dados de calibração das prensas para exibir no PDF
s.setdefault("cal_prensa_concreto_nome", "")
s.setdefault("cal_prensa_concreto_cert", "")
s.setdefault("cal_prensa_concreto_validade", "")
s.setdefault("cal_prensa_argamassa_nome", "")
s.setdefault("cal_prensa_argamassa_cert", "")
s.setdefault("cal_prensa_argamassa_validade", "")

# O sistema abre diretamente, sem depender de usuário ou senha salvos.

def _apply_query_prefs():
    try:
        qp = st.query_params
        def _first(x):
            if x is None: return None
            return x[0] if isinstance(x, list) else x
        theme = _first(qp.get("theme") or qp.get("t"))
        brand = _first(qp.get("brand") or qp.get("b"))
        if theme in ("Escuro moderno","Claro corporativo"): s["theme_mode"] = theme
        if brand in ("Laranja","Azul","Verde","Roxo"): s["brand"] = brand
    except Exception:
        pass
_apply_query_prefs()

s.setdefault("wide_layout", True)
MAX_W = 1800 if s.get("wide_layout") else 1300

# =============================================================================
# Estilo e tema
# =============================================================================
BRAND_MAP = {
    "Laranja": ("#f97316", "#ea580c", "#c2410c"),
    "Azul":    ("#3b82f6", "#2563eb", "#1d4ed8"),
    "Verde":   ("#22c55e", "#16a34a", "#15803d"),
    "Roxo":    ("#a855f7", "#9333ea", "#7e22ce"),
}
brand, brand600, brand700 = BRAND_MAP.get(s["brand"], BRAND_MAP["Laranja"])

plt.rcParams.update({
    "font.size":10,"axes.titlesize":12,"axes.labelsize":10,
    "axes.titleweight":"semibold","figure.autolayout":False
})

if s.get("theme_mode") == "Escuro moderno":
    plt.style.use("dark_background")
    css = f"""
    <style>
    :root {{
      --brand:{brand}; --brand-600:{brand600}; --brand-700:{brand700};
      --bg:#07111c; --surface:#0a1623; --panel:#0c1928; --panel2:#101f30;
      --text:#f8fafc; --muted:#94a3b8; --line:#21364a; --soft-line:rgba(148,163,184,.14);
      --shadow:0 16px 42px rgba(0,0,0,.20); --table-head:#102134; --table-row:#081521;
    }}
    .stApp, .main {{ background:linear-gradient(180deg,#06101a 0%,#08131f 45%,#07111c 100%) !important; color:var(--text)!important; }}
    .block-container{{ padding-top:18px!important; padding-bottom:48px!important; max-width:{MAX_W}px!important; }}
    section[data-testid="stSidebar"] > div{{background:linear-gradient(180deg,#07111c,#0a1623)!important;border-right:1px solid var(--line)!important;}}
    </style>
    """
else:
    plt.style.use("default")
    css = f"""
    <style>
    :root {{
      --brand:{brand}; --brand-600:{brand600}; --brand-700:{brand700};
      --bg:#f3f6f9; --surface:#ffffff; --panel:#ffffff; --panel2:#f8fafc;
      --text:#0f172a; --muted:#64748b; --line:#d8e1ea; --soft-line:rgba(15,23,42,.08);
      --shadow:0 14px 34px rgba(15,23,42,.08); --table-head:#f1f5f9; --table-row:#ffffff;
    }}
    .stApp, .main {{ background:linear-gradient(180deg,#f8fafc 0%,#f3f6f9 100%) !important; color:var(--text)!important; }}
    .block-container{{ padding-top:18px!important; padding-bottom:48px!important; max-width:{MAX_W}px!important; }}
    section[data-testid="stSidebar"] > div{{background:#ffffff!important;border-right:1px solid var(--line)!important;}}
    </style>
    """

css += f"""
<style>
html{{scroll-behavior:smooth}}
[data-testid="stHeader"]{{background:transparent!important}}
#MainMenu, footer{{visibility:hidden}}
hr{{border-color:var(--soft-line)!important}}

.h-card{{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px;padding:14px 15px;box-shadow:var(--shadow)}}
.h-kpi-label{{font-size:11px;color:var(--muted);font-weight:750;letter-spacing:.35px;text-transform:uppercase}}
.h-kpi{{font-size:22px;font-weight:900;color:var(--text);line-height:1.08;margin-top:5px}}
.pill{{display:inline-flex;gap:8px;align-items:center;padding:7px 11px;border-radius:999px;border:1px solid var(--line);background:var(--panel2);font-size:12.5px}}

.ui-statusbar{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:6px 0 16px;padding:9px 12px;border:1px solid var(--line);border-radius:12px;background:var(--panel);color:var(--muted);font-size:12px}}
.ui-statusbar b{{color:var(--text)}}

.ui-section-head{{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:18px 0 10px}}
.ui-section-head-left{{display:flex;align-items:center;gap:11px}}
.ui-section-ico{{width:34px;height:34px;border-radius:10px;display:flex;align-items:center;justify-content:center;background:rgba(249,115,22,.12);border:1px solid rgba(249,115,22,.28);font-size:17px}}
.ui-section-title{{font-size:18px;font-weight:900;color:var(--text);letter-spacing:-.2px}}
.ui-section-sub{{font-size:11.5px;color:var(--muted);margin-top:2px}}
.ui-chip{{font-size:11px;font-weight:800;color:#fb923c;padding:6px 9px;border:1px solid rgba(249,115,22,.25);background:rgba(249,115,22,.08);border-radius:999px;white-space:nowrap}}

.stTextInput input,.stNumberInput input,.stDateInput input{{background:var(--surface)!important;color:var(--text)!important;border:1px solid var(--line)!important;border-radius:10px!important}}
.stSelectbox div[data-baseweb="select"]>div,.stMultiSelect div[data-baseweb="select"]>div{{background:var(--surface)!important;color:var(--text)!important;border:1px solid var(--line)!important;border-radius:10px!important}}
.stTextInput label,.stNumberInput label,.stDateInput label,.stSelectbox label,.stMultiSelect label,.stRadio label,.stSlider label{{color:var(--muted)!important;font-weight:700!important}}

.stButton>button,.stDownloadButton>button{{background:linear-gradient(180deg,{brand},{brand600})!important;color:white!important;border:1px solid rgba(255,255,255,.06)!important;border-radius:11px!important;padding:10px 15px!important;font-weight:850!important;box-shadow:0 8px 18px rgba(0,0,0,.13)!important;transition:.18s ease!important}}
.stButton>button:hover,.stDownloadButton>button:hover{{transform:translateY(-1px);box-shadow:0 12px 24px rgba(0,0,0,.18)!important}}

[data-testid="stFileUploader"]{{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px;padding:12px 14px;box-shadow:var(--shadow)}}
[data-testid="stFileUploaderDropzone"]{{background:rgba(148,163,184,.045)!important;border:1px dashed rgba(249,115,22,.42)!important;border-radius:13px!important}}

.stTabs [data-baseweb="tab-list"]{{gap:7px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:6px;margin:2px 0 14px;box-shadow:0 8px 24px rgba(0,0,0,.06)}}
.stTabs [data-baseweb="tab"]{{height:42px;border-radius:10px;padding:0 16px;color:var(--muted);font-weight:850;background:transparent}}
.stTabs [aria-selected="true"]{{background:linear-gradient(180deg,rgba(249,115,22,.18),rgba(249,115,22,.08))!important;color:#fb923c!important}}
.stTabs [data-baseweb="tab-highlight"]{{display:none}}

.stExpander details{{border:1px solid var(--line)!important;border-radius:13px!important;background:var(--panel)!important;overflow:hidden}}
.stExpander details summary{{background:var(--panel2)!important;color:var(--text)!important;padding:10px 13px!important;font-weight:800!important}}

[data-testid="stDataFrame"]{{border:1px solid var(--line)!important;border-radius:14px!important;overflow:hidden!important;background:var(--panel)!important;box-shadow:0 8px 24px rgba(0,0,0,.06)!important}}
[data-testid="stDataFrame"] [role="columnheader"]{{background:var(--table-head)!important;font-weight:850!important}}

[data-testid="stAlert"]{{border-radius:12px!important;border:1px solid var(--line)!important;box-shadow:0 6px 20px rgba(0,0,0,.05)!important}}
[data-testid="stVerticalBlockBorderWrapper"]{{border-color:var(--line)!important;border-radius:16px!important;background:linear-gradient(180deg,var(--panel2),var(--panel))!important;box-shadow:0 10px 26px rgba(0,0,0,.07)!important}}

.ui-table-head{{display:flex;justify-content:space-between;align-items:flex-end;gap:15px;margin:6px 0 8px}}
.ui-table-title{{font-size:15px;font-weight:900;color:var(--text)}}
.ui-table-sub{{font-size:11px;color:var(--muted);margin-top:2px}}
.ui-table-count{{font-size:10.5px;color:var(--muted);border:1px solid var(--line);background:var(--panel2);padding:5px 8px;border-radius:999px;white-space:nowrap}}

.ui-chart-head{{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;margin:13px 0 7px;padding:0 2px}}
.ui-chart-title{{font-size:15px;font-weight:900;color:var(--text)}}
.ui-chart-sub{{font-size:11px;color:var(--muted);margin-top:2px}}
.ui-chart-tag{{font-size:10.5px;color:#fb923c;border:1px solid rgba(249,115,22,.24);background:rgba(249,115,22,.07);padding:5px 8px;border-radius:999px}}

.sidebar-brand{{padding:12px 12px 13px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(145deg,var(--panel2),var(--panel));margin:2px 0 14px}}
.sidebar-brand-title{{font-size:18px;font-weight:950;color:var(--text);letter-spacing:.5px}}
.sidebar-brand-title span{{color:#f97316}}
.sidebar-brand-sub{{font-size:10px;color:var(--muted);letter-spacing:.7px;margin-top:4px}}
section[data-testid="stSidebar"] .stExpander details{{box-shadow:none!important}}

.ov-kpis{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:3px 0 10px}}
.ov-card{{min-height:92px;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:15px;padding:13px 14px;box-shadow:0 9px 24px rgba(0,0,0,.07)}}
.ov-label{{font-size:10.5px;color:var(--muted);font-weight:800;text-transform:uppercase;letter-spacing:.35px}}
.ov-value{{font-size:19px;color:var(--text);font-weight:950;line-height:1.08;margin-top:8px;word-break:break-word}}
.ov-hint{{font-size:10px;color:var(--muted);margin-top:6px}}
.ov-tech{{display:grid;grid-template-columns:1.05fr 1.8fr 1fr;gap:10px;margin:10px 0}}
.ov-tech-card{{background:linear-gradient(180deg,rgba(249,115,22,.09),var(--panel));border:1px solid rgba(249,115,22,.30);border-radius:14px;padding:12px 14px}}
.ov-tech-label{{font-size:10px;color:#fb923c;font-weight:900;text-transform:uppercase;letter-spacing:.35px}}
.ov-tech-value{{font-size:14px;color:var(--text);font-weight:850;margin-top:5px;line-height:1.25}}
.ov-semaforo{{display:flex;align-items:center;justify-content:space-between;gap:14px;border:1px solid var(--line);background:var(--panel);border-radius:13px;padding:10px 13px;margin:10px 0 12px}}
.ov-semaforo-main{{font-size:13px;font-weight:900}}
.ov-semaforo-sub{{font-size:10.5px;color:var(--muted);margin-top:2px}}

.export-checks{{display:flex;flex-wrap:wrap;gap:7px;margin:5px 0 12px}}
.export-check{{font-size:11px;font-weight:800;padding:7px 9px;border-radius:999px;border:1px solid var(--line);background:var(--panel2)}}

@media(max-width:1400px){{.ov-kpis{{grid-template-columns:repeat(3,1fr)}}}}
@media(max-width:900px){{.ov-kpis{{grid-template-columns:repeat(2,1fr)}}.ov-tech{{grid-template-columns:1fr}}.ui-statusbar{{align-items:flex-start;flex-direction:column}}}}
</style>
"""
st.markdown(css, unsafe_allow_html=True)

# ============================================================================
# HABISOLUTE / DESIGN SYSTEM 2026 — apresentação apenas, sem alterar regras
# ============================================================================
st.markdown("""
<style>
:root { --h-orange:#f97316;--h-orange-soft:rgba(249,115,22,.12);--h-green:#31c48d;--h-danger:#fb7185; }
html,body,[data-testid="stAppViewContainer"] { font-family:Inter,"Segoe UI",Arial,sans-serif; }
.stApp { background:var(--bg)!important; }
.block-container { padding-top:1.0rem!important; padding-bottom:4rem!important; }
[data-testid="stHeader"] { height:2.5rem; }
.habi-hero { display:flex;align-items:center;gap:18px;min-height:114px;padding:23px 27px;
    background:linear-gradient(120deg,#111d2b,#152537 58%,#1e2a36);border:1px solid #293849;
    border-radius:20px;margin:2px 0 18px;box-shadow:0 12px 28px rgba(0,0,0,.12); }
.habi-brand-mark {width:55px;height:55px;flex:none;display:flex;align-items:center;justify-content:center;
    border-radius:14px;background:linear-gradient(145deg,#fb923c,#ea580c);font-size:33px;color:white;
    font-weight:900;box-shadow:0 8px 22px rgba(249,115,22,.18);}
.habi-brand-copy {flex:1;min-width:0}.habi-eyebrow {font-size:10px;font-weight:750;letter-spacing:1.8px;color:#b6c5d7}
.habi-hero-title {font-size:30px;letter-spacing:-1.2px;color:#f8fafc;font-weight:850;line-height:1.32}
.habi-hero-title span {font-size:13px;font-weight:550;color:#fb923c;letter-spacing:0;margin-left:11px}
.habi-hero-sub {font-size:12px;color:#a9bacb;margin-top:2px}
.habi-hero-right {border:1px solid #31465a;color:#e4edf7;border-radius:12px;padding:11px 15px;font-size:10px;
    font-weight:800;letter-spacing:1.0px;white-space:nowrap}
.habi-hero-right small {display:block;color:#9aadc2;font-size:9px;margin:6px 0 0 17px;letter-spacing:.8px}
.habi-dot {width:7px;height:7px;background:#34d399;border-radius:50%;display:inline-block;margin-right:7px}
.ui-statusbar { margin:0 0 21px!important; padding:8px 13px!important;border-radius:9px!important;
    box-shadow:none!important; font-size:11px!important; }
.ui-section-head { margin:31px 0 13px!important;padding-bottom:12px;border-bottom:1px solid var(--soft-line); }
.ui-section-ico { background:var(--h-orange-soft)!important;border:0!important;border-radius:10px!important;width:39px!important;height:39px!important; }
.ui-section-title {font-size:19px!important;font-weight:800!important;letter-spacing:-.5px!important}
.ui-section-sub {font-size:12px!important;margin-top:4px!important;line-height:1.5}
.ui-chip,.ui-chart-tag,.ov-tech-label { color:var(--h-orange)!important; }
.h-card,.ov-card,.ov-tech-card,[data-testid="stVerticalBlockBorderWrapper"] {box-shadow:none!important;
    background:var(--panel)!important;border:1px solid var(--line)!important;border-radius:14px!important;}
.ov-kpis {gap:12px!important;margin-top:15px!important}.ov-card {padding:18px!important;min-height:107px!important;
    border-top:2px solid rgba(249,115,22,.45)!important}
.ov-label,.h-kpi-label {font-size:11px!important;letter-spacing:.6px!important}
.ov-value {font-size:25px!important;margin-top:12px!important;letter-spacing:-.7px}
.ov-hint {font-size:11px!important}
.ov-tech-card {border-left:3px solid var(--h-orange)!important}
.ui-semaforo,.ov-semaforo {border-radius:12px!important}
.stButton>button { background:var(--panel2)!important;color:var(--text)!important;border:1px solid var(--line)!important;
    box-shadow:none!important;font-weight:700!important;border-radius:10px!important;}
.stButton>button[kind="primary"],.stButton>button[data-testid="stBaseButton-primary"],
.stDownloadButton>button {background:#ea580c!important;color:#fff!important;border:1px solid #ea580c!important;
    box-shadow:none!important;border-radius:10px!important;font-weight:750!important;}
.stButton>button:hover {color:var(--h-orange)!important;border-color:var(--h-orange)!important;transform:none!important;box-shadow:none!important}
.stDownloadButton>button:hover,.stButton>button[kind="primary"]:hover {background:#c2410c!important;color:white!important;transform:none!important}
.stTextInput input,.stNumberInput input,.stDateInput input,.stTextArea textarea {min-height:42px;border-radius:9px!important}
.stSelectbox div[data-baseweb="select"]>div,.stMultiSelect div[data-baseweb="select"]>div {min-height:42px;border-radius:9px!important}
[data-testid="stFileUploader"] {border:1px solid var(--line)!important;box-shadow:none!important;padding:15px!important;background:var(--panel)!important}
[data-testid="stFileUploaderDropzone"] {border:1.5px dashed rgba(249,115,22,.55)!important;background:var(--panel2)!important;padding:14px!important}
.stTabs [data-baseweb="tab-list"] {box-shadow:none!important;border-radius:11px!important;padding:5px!important;gap:4px!important}
.stTabs [data-baseweb="tab"] {border-radius:8px!important;font-size:12px!important;padding:0 13px!important;font-weight:700!important}
.stTabs [aria-selected="true"] {color:var(--text)!important;background:var(--h-orange-soft)!important;box-shadow:inset 0 -2px 0 var(--h-orange)!important}
.stExpander details,[data-testid="stDataFrame"] {box-shadow:none!important;border-radius:11px!important}
.ui-chart-head {border:1px solid var(--line)!important;border-left:3px solid var(--h-orange)!important;
    border-radius:10px!important;background:var(--panel)!important;box-shadow:none!important;padding:12px 14px!important;}
.ui-chart-title,.ui-table-title {font-size:15px!important;letter-spacing:-.2px}
.ui-chart-tag {border-color:var(--line)!important;background:var(--panel2)!important;box-shadow:none!important}
.ui-table-head {padding-top:12px!important}
section[data-testid="stSidebar"]>div {background:var(--surface)!important}
section[data-testid="stSidebar"] .block-container {padding-top:20px!important}
.sidebar-brand {background:transparent!important;box-shadow:none!important;border:none!important;
    border-bottom:1px solid var(--line)!important;border-radius:0!important;padding:14px 4px 19px!important}
.sidebar-brand-title {font-size:20px!important;letter-spacing:1px!important}
.sidebar-brand-sub {letter-spacing:1.5px!important;font-size:10px!important}
section[data-testid="stSidebar"] h3 {font-size:14px!important}
[data-testid="stAlert"] {box-shadow:none!important;border-radius:10px!important}
@media(max-width:1000px){.habi-hero-right{display:none}.habi-hero-title span{display:block;margin-left:0}.habi-hero{padding:18px}}
@media(max-width:600px){.habi-hero-title{font-size:24px}.habi-brand-mark{width:44px;height:44px;font-size:27px}.habi-eyebrow{font-size:8px}.habi-hero{gap:12px}}
</style>
""",unsafe_allow_html=True)


# DESIGN SYSTEM V3 — nova composição visual, mantendo as rotinas e dados intactos.
st.markdown("""
<style>
:root {--hx-ink:#0b1220;--hx-accent:#ff792d;--hx-cyan:#22d3ee;--hx-border:#263347}
html {scroll-behavior:smooth}
[data-testid="stAppViewContainer"]>.main {background:linear-gradient(135deg,#08111d 0%,#101c2c 55%,#0a1421 100%)!important}
.stApp {--bg:#0b1422!important;--surface:#111e2e!important;--panel:#142235!important;--panel2:#1a293c!important;--text:#f4f7fc!important;--muted:#a1aec2!important;--line:#304056!important;--soft-line:rgba(160,181,208,.16)!important;color:#f4f7fc!important}
[data-testid="stSidebar"]>div {background:#0c1726!important;border-right:1px solid #2c3b4e!important}
.block-container {max-width:1680px!important;padding:12px 38px 80px!important}
[data-testid="stHeader"] {background:transparent!important}
.hx-top {min-height:75px;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:10px 5px 18px;border-bottom:1px solid #273951;margin-bottom:22px}
.hx-topbrand {display:flex;align-items:center;gap:13px}.hx-logo {width:47px;height:47px;display:grid;place-items:center;border-radius:13px;background:#ff792d;color:#fff;font-size:27px;font-weight:900}.hx-logo span {font-size:17px;margin-left:-3px;color:#203044}
.hx-company{font-size:20px;color:#fff;font-weight:900;letter-spacing:1px}.hx-company em{font-style:normal;color:#ff792d;font-size:12px;letter-spacing:2px;margin-left:8px}.hx-tagline{font-size:10px;color:#98a9be;letter-spacing:1.2px;margin-top:4px}
.hx-topright {font-size:10px;color:#b9c6d7;font-weight:800;letter-spacing:1.1px}.hx-live{display:inline-block;width:7px;height:7px;border-radius:50%;background:#30d7a3;box-shadow:0 0 0 5px rgba(48,215,163,.10);margin-right:7px}.hx-version{padding:7px 11px;border:1px solid #3a485c;border-radius:7px;margin-left:15px;color:#f1f5fb}
.hx-welcome{position:relative;display:flex;justify-content:space-between;gap:24px;align-items:center;overflow:hidden;background:radial-gradient(circle at 84% 34%,rgba(255,121,45,.23),transparent 35%),linear-gradient(110deg,#182a42,#142236 62%,#243044);border:1px solid #37465d;border-radius:22px;padding:36px 42px;min-height:220px;box-shadow:0 20px 45px rgba(0,0,0,.20)}
.hx-overline{font-size:11px;font-weight:850;color:#ffab72;letter-spacing:2px;margin-bottom:14px}.hx-welcome h1{font-size:clamp(27px,3vw,43px);line-height:1.1;font-weight:900;letter-spacing:-1.6px;color:#fff;margin:0 0 15px}.hx-welcome h1 span{color:#ff914f}.hx-welcome p{color:#adbed0;font-size:13px;margin:0;max-width:580px;line-height:1.7}
.hx-welcome-art{display:flex;align-items:flex-end;flex-direction:column;gap:7px;min-width:255px}.hx-welcome-art strong{font-size:10px;letter-spacing:1.2px;color:#cbd9e7}.hx-welcome-art small{font-size:11px;color:#95a9bf}
.hx-pulse{display:flex;gap:8px;height:90px;align-items:flex-end;margin-bottom:10px}.hx-pulse span{width:19px;border-radius:5px 5px 0 0;background:linear-gradient(0deg,#ef6a1f,#ffad68);height:38%}.hx-pulse span:nth-child(2){height:59%}.hx-pulse span:nth-child(3){height:42%}.hx-pulse span:nth-child(4){height:80%;background:linear-gradient(0deg,#0e9ab6,#43e0ee)}.hx-pulse span:nth-child(5){height:69%}.hx-pulse span:nth-child(6){height:91%;background:linear-gradient(0deg,#0e9ab6,#43e0ee)}.hx-pulse span:nth-child(7){height:74%}.hx-pulse span:nth-child(8){height:100%;background:linear-gradient(0deg,#0e9ab6,#43e0ee)}
.hx-jump{display:flex;flex-wrap:wrap;align-items:center;gap:6px;background:#111e30;border:1px solid #283a52;border-radius:13px;margin:14px 0 26px;padding:9px 12px}.hx-jump>span{font-size:10px;letter-spacing:1px;font-weight:850;color:#788fa8;padding:0 14px 0 4px}.hx-jump a{display:inline-flex;color:#bdcfe2!important;text-decoration:none!important;border-radius:8px;padding:10px 13px;font-size:12px;font-weight:750;transition:background .2s}.hx-jump a:hover{background:#243750;color:#ff995c!important}
.ui-statusbar{display:none!important}
.ui-section-head{position:relative;background:#121f31!important;padding:20px 22px!important;border:1px solid #304056!important;border-radius:15px!important;margin:34px 0 16px!important;box-shadow:0 9px 30px rgba(0,0,0,.10)!important;border-bottom:1px solid #304056!important}
.ui-section-title{font-size:22px!important;font-weight:900!important;color:#f4f7fc!important}.ui-section-sub{font-size:12px!important;color:#94a7bd!important}.ui-section-ico{background:#2b3443!important;color:#ff984c!important;border-radius:11px!important;width:47px!important;height:47px!important;font-size:21px!important}.ui-chip{background:#332819!important;color:#ffb477!important;border:1px solid #624328!important;padding:8px 11px!important}
.ov-kpis{grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:15px!important}.ov-kpis[style*="repeat(4"]{grid-template-columns:repeat(4,minmax(0,1fr))!important}
.ov-card{background:#17263a!important;border:1px solid #31445c!important;border-top:0!important;border-left:4px solid #ff873e!important;border-radius:14px!important;min-height:128px!important;padding:21px!important;box-shadow:0 10px 28px rgba(0,0,0,.12)!important}.ov-card:nth-child(3n+2){border-left-color:#22d3ee!important}.ov-card:nth-child(3n){border-left-color:#34d399!important}.ov-label{font-size:11px!important;color:#a7bdd4!important}.ov-value{color:#f8fafc!important;font-size:26px!important;font-weight:900!important;margin-top:15px!important}.ov-hint{color:#849db5!important}
.ov-tech-card {background:#17263a!important;border:1px solid #31445c!important;border-top:3px solid #ff873e!important;border-left:1px solid #31445c!important;padding:18px!important}
.stTabs [data-baseweb="tab-list"]{background:#17263a!important;border:1px solid #34445b!important;border-radius:12px!important;padding:7px!important}.stTabs [data-baseweb="tab"]{height:46px!important;color:#b4c6dc!important;font-size:13px!important}.stTabs [aria-selected="true"]{background:#ff792d!important;color:#fff!important;box-shadow:none!important}
[data-testid="stFileUploader"]{background:#16283d!important;border:1px solid #3d5169!important;padding:18px!important;border-radius:16px!important}[data-testid="stFileUploaderDropzone"]{background:#1b3148!important;border:2px dashed #ff8e49!important;border-radius:13px!important;padding:27px!important}
.stButton>button,.stDownloadButton>button{min-height:40px!important}.stDownloadButton>button{background:#f97316!important}
.ui-chart-head{background:#192a40!important;border:1px solid #3d516b!important;border-left:4px solid #22d3ee!important;padding:17px!important;border-radius:14px!important}.ui-chart-title{font-size:17px!important}.ui-chart-tag{color:#72e5ff!important;background:#153344!important}
.ui-table-head{background:#1a2b42!important;border-radius:12px 12px 0 0!important;border:1px solid #354961!important;padding:16px!important;margin-bottom:0!important}.ui-table-title{color:#f1f6fd!important;font-size:17px!important}.ui-table-sub{color:#a5b8cb!important}.ui-table-count{background:#273a50!important;color:#d2e1ef!important}
[data-testid="stDataFrame"]{border:1px solid #364960!important;border-radius:0 0 12px 12px!important}
section[data-testid="stSidebar"] .sidebar-brand-title{font-size:22px!important;color:white!important}
section[data-testid="stSidebar"] label,section[data-testid="stSidebar"] p{color:#c6d5e4!important}
.stTextInput input,.stNumberInput input,.stDateInput input,.stSelectbox div[data-baseweb="select"]>div{background:#16263b!important;color:#f1f5fc!important;border:1px solid #3b506b!important}
@media(max-width:1100px){.hx-welcome-art{display:none}.ov-kpis,.ov-kpis[style*="repeat(4"]{grid-template-columns:repeat(2,minmax(0,1fr))!important}.block-container{padding-left:20px!important;padding-right:20px!important}}
@media(max-width:650px){.hx-topright{display:none}.hx-welcome{padding:26px 22px;min-height:0}.ov-kpis,.ov-kpis[style*="repeat(4"]{grid-template-columns:1fr!important}.hx-jump{gap:2px}.hx-jump a{font-size:11px;padding:8px}.hx-company{font-size:15px}.hx-tagline{font-size:8px}}
</style>
""", unsafe_allow_html=True)

def _ui_section(title: str, subtitle: str = "", icon: str = "◆", chip: str = "") -> str:
    import html as _html
    chip_html = f'<div class="ui-chip">{_html.escape(chip)}</div>' if chip else ""
    return f"""
    <div class="ui-section-head">
      <div class="ui-section-head-left">
        <div class="ui-section-ico">{icon}</div>
        <div><div class="ui-section-title">{_html.escape(title)}</div><div class="ui-section-sub">{_html.escape(subtitle)}</div></div>
      </div>
      {chip_html}
    </div>
    """

def render_screen_table(df_: pd.DataFrame, title: str, subtitle: str = "", height: Optional[int] = None, hide_index: bool = True):
    import html as _html
    if df_ is None:
        return
    nrows = len(df_) if hasattr(df_, "__len__") else 0
    st.markdown(
        f'<div class="ui-table-head"><div><div class="ui-table-title">{_html.escape(title)}</div><div class="ui-table-sub">{_html.escape(subtitle)}</div></div><div class="ui-table-count">{nrows} linha(s)</div></div>',
        unsafe_allow_html=True
    )
    kwargs = dict(use_container_width=True, hide_index=hide_index)
    if height is not None:
        kwargs["height"] = height
    st.dataframe(df_, **kwargs)

def render_screen_chart(fig, title: str, subtitle: str = "", tag: str = "ANÁLISE"):
    """Renderização tecnológica somente na tela; o figure original continua intacto para PDFs/exportações."""
    import copy
    import html as _html
    import matplotlib.patheffects as _pe
    from matplotlib.collections import PolyCollection, LineCollection

    if fig is None:
        return

    st.markdown(
        f"""<div class="ui-chart-head" style="padding:10px 12px;border:1px solid rgba(0,229,255,.16);border-radius:13px;background:linear-gradient(90deg,rgba(0,229,255,.055),rgba(139,92,246,.035),transparent);box-shadow:inset 3px 0 0 rgba(0,229,255,.75)">
        <div><div class="ui-chart-title">{_html.escape(title)}</div><div class="ui-chart-sub">{_html.escape(subtitle)}</div></div>
        <div class="ui-chart-tag" style="color:#00e5ff;border-color:rgba(0,229,255,.30);background:rgba(0,229,255,.08);box-shadow:0 0 16px rgba(0,229,255,.08)">{_html.escape(tag)}</div></div>""",
        unsafe_allow_html=True
    )

    try:
        fscreen = copy.deepcopy(fig)

        # Paleta neon/tecnológica exclusiva da visualização em tela.
        palette = [
            "#00E5FF",  # cyan
            "#FF8A00",  # laranja vivo
            "#00E676",  # verde neon
            "#8B5CF6",  # violeta
            "#F472B6",  # rosa técnico
            "#2F80FF",  # azul elétrico
            "#FFD60A",  # amarelo
            "#A3E635",  # verde claro
            "#FF5E7A",  # coral
            "#22D3EE",  # turquesa
        ]
        tech_bg = "#050B12"
        panel_bg = "#07111D"
        text_main = "#F3F8FF"
        text_muted = "#8FA7BE"
        grid_major = "#244258"
        grid_minor = "#173044"
        spine = "#2A4A60"

        fscreen.patch.set_facecolor(tech_bg)
        fscreen.patch.set_edgecolor("#16364A")
        fscreen.patch.set_linewidth(1.0)

        for ax in fscreen.axes:
            ax.set_facecolor(panel_bg)
            ax.title.set_color(text_main)
            ax.title.set_fontweight("bold")
            ax.title.set_fontsize(max(ax.title.get_fontsize(), 12))
            ax.xaxis.label.set_color(text_muted)
            ax.yaxis.label.set_color(text_muted)
            ax.tick_params(axis="both", colors=text_muted, labelsize=9, length=4, width=.8)

            # Moldura fina com aspecto de painel técnico.
            for sp in ax.spines.values():
                sp.set_color(spine)
                sp.set_alpha(.85)
                sp.set_linewidth(.9)

            # Grade dupla: principal + secundária, discreta.
            ax.minorticks_on()
            ax.grid(True, which="major", linestyle="--", linewidth=.75, alpha=.50, color=grid_major)
            ax.grid(True, which="minor", linestyle=":", linewidth=.45, alpha=.24, color=grid_minor)
            ax.set_axisbelow(True)

            # Recolore as séries usando o significado da legenda sempre que possível.
            normal_idx = 0
            for line in ax.get_lines():
                label = str(line.get_label() or "")
                low = label.lower()

                if "fck" in low or "projeto" in low:
                    color = "#FF3B5C"
                    line.set_linestyle(":")
                    line.set_linewidth(max(line.get_linewidth(), 2.4))
                elif "média" in low or "media" in low or ("real" in low and "cp " not in low):
                    color = "#00E5FF"
                    line.set_linewidth(max(line.get_linewidth(), 2.5))
                elif "estim" in low or "curva estimada" in low:
                    color = "#FF8A00"
                    line.set_linewidth(max(line.get_linewidth(), 2.2))
                else:
                    color = palette[normal_idx % len(palette)]
                    normal_idx += 1
                    line.set_linewidth(max(line.get_linewidth(), 1.9))

                line.set_color(color)
                line.set_alpha(.98)
                if line.get_marker() not in (None, "None", "", " "):
                    line.set_markerfacecolor(color)
                    line.set_markeredgecolor("#EAF7FF")
                    line.set_markeredgewidth(.8)
                    line.set_markersize(max(float(line.get_markersize() or 0), 6.2))

                # Glow sutil para destacar as curvas sem comprometer leitura técnica.
                try:
                    lw = float(line.get_linewidth())
                    line.set_path_effects([
                        _pe.Stroke(linewidth=lw + 4.0, foreground=color, alpha=.10),
                        _pe.Stroke(linewidth=lw + 1.8, foreground=color, alpha=.15),
                        _pe.Normal(),
                    ])
                except Exception:
                    pass

            # Faixas de desvio padrão e linhas verticais também recebem tratamento visual.
            for coll in ax.collections:
                label = str(coll.get_label() or "").lower()
                try:
                    if isinstance(coll, PolyCollection):
                        fill = "#00E5FF" if ("dp" in label or "real" in label) else "#8B5CF6"
                        coll.set_facecolor(fill)
                        coll.set_edgecolor(fill)
                        coll.set_alpha(.12)
                    elif isinstance(coll, LineCollection):
                        coll.set_color("#4CC9F0")
                        coll.set_alpha(.45)
                        coll.set_linewidth(.9)
                except Exception:
                    pass

            # Textos anotados (ex.: valores da curva estimada).
            for t in ax.texts:
                t.set_color("#DDF7FF")
                t.set_fontweight("bold")
                try:
                    t.set_bbox(dict(boxstyle="round,pad=0.22", facecolor="#0A1A27", edgecolor="#21465C", alpha=.92))
                except Exception:
                    pass

            ax.margins(x=.035, y=.10)

            leg = ax.get_legend()
            if leg is not None:
                frame = leg.get_frame()
                frame.set_facecolor("#081522")
                frame.set_edgecolor("#24465D")
                frame.set_alpha(.97)
                for t in leg.get_texts():
                    t.set_color("#DDEBFA")
                    t.set_fontsize(9)

        st.pyplot(fscreen, use_container_width=True)
        plt.close(fscreen)
    except Exception:
        st.pyplot(fig, use_container_width=True)

def _render_header():
    st.markdown("""
    <div class="hx-top">
      <div class="hx-topbrand"><span class="hx-logo">H<span>◆</span></span>
        <div><div class="hx-company">HABISOLUTE <em>ANALYTICS</em></div>
        <div class="hx-tagline">ENGENHARIA · INTELIGÊNCIA DE CONTROLE TECNOLÓGICO</div></div>
      </div>
      <div class="hx-topright"><span class="hx-live"></span> AMBIENTE DE ANÁLISE
        <span class="hx-version">PAINEL V3</span></div>
    </div>
    <div class="hx-welcome">
       <div><div class="hx-overline">CENTRAL DE INTELIGÊNCIA LABORATORIAL</div>
       <h1>Controle tecnológico <span>em tempo real.</span></h1>
       <p>Importe certificados, encontre divergências e acompanhe a resistência do concreto em um só lugar.</p></div>
       <div class="hx-welcome-art"><div class="hx-pulse"><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span></div>
       <strong>ANÁLISE DE RESISTÊNCIA</strong><small>Precisão e rastreabilidade</small></div>
    </div>
    <div class="hx-jump"><span>ACESSO RÁPIDO</span>
      <a href="#upload-area">01 &nbsp; Importação</a><a href="#technical-alerts">02 &nbsp; Alertas</a>
      <a href="#attention-map">03 &nbsp; Mapa de CPs</a><a href="#graphs">04 &nbsp; Gráficos</a>
      <a href="#pair-analysis">05 &nbsp; Tabela técnica</a></div>
    """, unsafe_allow_html=True)

# =============================================================================
# Autenticação & gerenciamento de usuários
# =============================================================================
def _hash_password(pw: str) -> str:
    return hashlib.sha256(("habisolute|" + pw).encode("utf-8")).hexdigest()

def _verify_password(pw: str, hashed: str) -> bool:
    try:
        return _hash_password(pw) == hashed
    except Exception:
        return False

def _save_users(data: Dict[str, Any]) -> None:
    tmp = USERS_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(USERS_DB)

def _load_users() -> Dict[str, Any]:
    def _bootstrap_admin(db: Dict[str, Any]) -> Dict[str, Any]:
        db.setdefault("users", {})
        if "admin" not in db["users"]:
            db["users"]["admin"] = {
                "password": _hash_password("1234"),
                "is_admin": True,
                "active": True,
                "must_change": True,
                "created_at": datetime.now().isoformat(timespec="seconds")
            }
        return db
    try:
        if USERS_DB.exists():
            raw = USERS_DB.read_text(encoding="utf-8").strip()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict) and isinstance(data.get("users"), dict):
                    fixed = _bootstrap_admin(data)
                    if fixed is not data: _save_users(fixed)
                    return fixed
                if isinstance(data, dict):
                    fixed = _bootstrap_admin({"users": data}); _save_users(fixed); return fixed
                if isinstance(data, list):
                    users_map: Dict[str, Any] = {}
                    for item in data:
                        if isinstance(item, str):
                            uname = item.strip()
                            if not uname: continue
                            users_map[uname] = {
                                "password": _hash_password("1234"),
                                "is_admin": (uname == "admin"),
                                "active": True,
                                "must_change": True,
                                "created_at": datetime.now().isoformat(timespec="seconds")
                            }
                        elif isinstance(item, dict) and item.get("username"):
                            uname = str(item["username"]).strip()
                            if not uname: continue
                            users_map[uname] = {
                                "password": _hash_password("1234"),
                                "is_admin": bool(item.get("is_admin", uname == "admin")),
                                "active": bool(item.get("active", True)),
                                "must_change": True,
                                "created_at": item.get("created_at", datetime.now().isoformat(timespec="seconds"))
                            }
                    fixed = _bootstrap_admin({"users": users_map}); _save_users(fixed); return fixed
    except Exception:
        pass
    default = _bootstrap_admin({"users": {}}); _save_users(default); return default

def user_get(username: str) -> Optional[Dict[str, Any]]:
    return _load_users().get("users", {}).get(username)

def user_set(username: str, record: Dict[str, Any]) -> None:
    db = _load_users(); db.setdefault("users", {})[username] = record; _save_users(db)

def user_exists(username: str) -> bool:
    return user_get(username) is not None

def user_list() -> List[Dict[str, Any]]:
    db = _load_users(); out = []
    for uname, rec in db.get("users", {}).items():
        r = dict(rec); r["username"] = uname; out.append(r)
    out.sort(key=lambda r: (not r.get("is_admin", False), r["username"]))
    return out

def user_delete(username: str) -> None:
    db = _load_users()
    if username in db.get("users", {}):
        if username == "admin":
            return
        db["users"].pop(username, None); _save_users(db)

def _auth_login_ui():
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>🔐 Entrar - 🏗️ Habisolute Analytics</div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1.3, 1.3, 0.7])
    with c1:
        user = st.text_input("Usuário", key="login_user", label_visibility="collapsed", placeholder="Usuário")
    with c2:
        pwd = st.text_input("Senha", key="login_pass", type="password", label_visibility="collapsed", placeholder="Senha")
    with c3:
        st.markdown("<div style='height:2px'></div>", unsafe_allow_html=True)
        if st.button("Acessar", use_container_width=True):
            rec = user_get((user or "").strip())
            if not rec or not rec.get("active", True):
                st.error("Usuário inexistente ou inativo.")
                log_event("login_fail", {"username": user, "reason": "not_found_or_inactive"}, level="WARN")
            elif not _verify_password(pwd, rec.get("password","")):
                st.error("Senha incorreta.")
                log_event("login_fail", {"username": user, "reason": "bad_password"}, level="WARN")
            else:
                s["logged_in"] = True; s["username"] = (user or "").strip()
                s["is_admin"] = bool(rec.get("is_admin", False)); s["must_change"] = bool(rec.get("must_change", False))
                prefs = load_user_prefs(); prefs["last_user"] = s["username"]; save_user_prefs(prefs)
                log_event("login_success", {"username": s["username"]})
                st.rerun()
    st.caption("Primeiro acesso: **admin / 1234** (será exigida troca de senha).")
    st.markdown("</div>", unsafe_allow_html=True)

def _force_change_password_ui(username: str):
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>🔑 Definir nova senha</div>", unsafe_allow_html=True)
    p1 = st.text_input("Nova senha", type="password"); p2 = st.text_input("Confirmar nova senha", type="password")
    if st.button("Salvar nova senha", use_container_width=True):
        if len(p1) < 4:
            st.error("Use ao menos 4 caracteres.")
        elif p1 != p2:
            st.error("As senhas não conferem.")
        else:
            rec = user_get(username) or {}
            rec["password"] = _hash_password(p1); rec["must_change"] = False; user_set(username, rec)
            log_event("password_changed", {"username": username})
            st.success("Senha atualizada! Redirecionando…"); s["must_change"] = False; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# Acesso direto (tela de login removida)
# =============================================================================

# Cabeçalho
_render_header()
# =============================================================================
# Aparência do painel
# =============================================================================
with st.expander("🎨 Aparência e preferências do painel", expanded=False):
    c1, c2, c3 = st.columns([1.2, 1.2, 1.0])
    with c1:
        s["theme_mode"] = st.radio(
            "Tema", ["Escuro moderno","Claro corporativo"],
            index=0 if s.get("theme_mode")=="Escuro moderno" else 1,
            horizontal=True
        )
    with c2:
        s["brand"] = st.selectbox(
            "Cor de destaque", ["Laranja","Azul","Verde","Roxo"],
            index=["Laranja","Azul","Verde","Roxo"].index(s.get("brand","Laranja"))
        )
    with c3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("💾 Salvar como padrão", use_container_width=True, key="k_save"):
            save_user_prefs({"theme_mode": s["theme_mode"], "brand": s["brand"]})
            try:
                qp = st.query_params
                qp.update({"theme": s["theme_mode"], "brand": s["brand"]})
            except Exception:
                pass
            st.success("Preferências salvas.")

nome_login = "Habisolute"
papel = "Acesso direto"
st.markdown(
    f'<div class="ui-statusbar"><div><b>Habisolute Analytics</b> • ambiente de controle tecnológico</div><div>Usuário: <b>{nome_login}</b> • {papel}</div></div>',
    unsafe_allow_html=True
)

# Sem login, todas as ferramentas e exportações ficam disponíveis.
CAN_ADMIN = True
CAN_EXPORT = True

def _empty_audit_df():
    return pd.DataFrame(columns=["ts", "user", "level", "action", "meta"])

df_log = _empty_audit_df()

if False:  # Painel de usuários desativado porque o login foi removido.
    with st.expander("👤 Painel de Usuários (Admin)", expanded=False):
        st.markdown("Cadastre, ative/desative e redefina senhas dos usuários do sistema.")
        tab1, tab2, tab3 = st.tabs(["Usuários", "Novo usuário", "Auditoria"])

        with tab1:
            users = user_list()
            if not users:
                st.info("Nenhum usuário cadastrado.")
            else:
                for u in users:
                    colA, colB, colC, colD, colE = st.columns([2,1,1.2,1.6,1.4])
                    colA.write(f"**{u['username']}**")
                    colB.write("👑 Admin" if u.get("is_admin") else "Usuário")
                    colC.write("✅ Ativo" if u.get("active", True) else "❌ Inativo")
                    colD.write(("Exige troca" if u.get("must_change") else "Senha OK"))
                    with colE:
                        if u["username"] != "admin":
                            if st.button(("Desativar" if u.get("active", True) else "Reativar"), key=f"act_{u['username']}"):
                                rec = user_get(u["username"]) or {}
                                rec["active"] = not rec.get("active", True)
                                user_set(u["username"], rec)
                                st.rerun()
                            if st.button("Redefinir", key=f"rst_{u['username']}"):
                                rec = user_get(u["username"]) or {}
                                rec["password"] = _hash_password("1234")
                                rec["must_change"] = True
                                user_set(u["username"], rec)
                                st.rerun()
                            if st.button("Excluir", key=f"del_{u['username']}"):
                                user_delete(u["username"])
                                st.rerun()

        with tab2:
            st.markdown("### Novo usuário")
            new_u = st.text_input("Usuário (login)")
            is_ad = st.checkbox("Admin?", value=False)
            if st.button("Criar usuário", key="btn_new_user"):
                if not new_u.strip():
                    st.error("Informe o nome do usuário.")
                elif user_exists(new_u.strip()):
                    st.error("Usuário já existe.")
                else:
                    user_set(new_u.strip(), {
                        "password": _hash_password("1234"),
                        "is_admin": bool(is_ad),
                        "active": True,
                        "must_change": True,
                        "created_at": datetime.now().isoformat(timespec="seconds")
                    })
                    log_event("user_created", {"created_user": new_u.strip(), "is_admin": bool(is_ad)})
                    st.success("Usuário criado com senha inicial 1234 (forçará troca no primeiro acesso).")
                    st.rerun()

        with tab3:
            st.markdown("### Auditoria do Sistema")
            df_log = read_audit_df()
            if df_log.empty:
                st.info("Sem eventos de auditoria ainda.")
            else:
                try:
                    _d = pd.to_datetime(df_log["ts"].str.replace("Z", "", regex=False), errors="coerce").dt.date
                    hoje = datetime.utcnow().date()
                    tot_ev = int(len(df_log))
                    tot_usr = int(df_log["user"].nunique())
                    tot_act = int(df_log["action"].nunique())
                    tot_hoje = int((_d == hoje).sum())
                except Exception:
                    tot_ev = len(df_log); tot_usr = 0; tot_act = 0; tot_hoje = 0

                st.markdown(
                    f"""
                    <div style="display:flex;gap:10px;flex-wrap:wrap;margin:6px 0 10px 0">
                      <div class="h-card"><div class="h-kpi-label">Eventos</div><div class="h-kpi">{tot_ev}</div></div>
                      <div class="h-card"><div class="h-kpi-label">Por usuário</div><div class="h-kpi">{tot_usr}</div></div>
                      <div class="h-card"><div class="h-kpi-label">Por ação</div><div class="h-kpi">{tot_act}</div></div>
                      <div class="h-card"><div class="h-kpi-label">Hoje</div><div class="h-kpi">{tot_hoje}</div></div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                c1_, c2_, c3_, c4_ = st.columns([1.4, 1.2, 1.6, 1.0])
                with c1_:
                    users_opt = ["(Todos)"] + sorted([u for u in df_log["user"].dropna().unique().tolist()])
                    f_user = st.selectbox("Usuário", users_opt, index=0)
                with c2_:
                    f_action = st.text_input("Ação contém...", "")
                with c3_:
                    lv_opts = ["(Todos)", "INFO", "WARN", "ERROR"]
                    f_level = st.selectbox("Nível", lv_opts, index=0)
                with c4_:
                    page_size = st.selectbox("Linhas", [100, 300, 1000], index=1)

                d1_, d2_ = st.columns(2)
                with d1_:
                    dt_min = st.date_input("Data inicial", value=None, key="aud_dini")
                with d2_:
                    dt_max = st.date_input("Data final", value=None, key="aud_dfim")

                logv = df_log.copy()
                if f_user and f_user != "(Todos)":
                    logv = logv[logv["user"] == f_user]
                if f_action:
                    logv = logv[logv["action"].str.contains(f_action, case=False, na=False)]
                if f_level and f_level != "(Todos)":
                    logv = logv[logv["level"] == f_level]

                if "ts" in logv.columns:
                    logv["_d"] = pd.to_datetime(logv["ts"].str.replace("Z", "", regex=False), errors="coerce").dt.date
                    if dt_min:
                        logv = logv[logv["_d"].apply(lambda d: (d is not None) and (d >= dt_min))]
                    if dt_max:
                        logv = logv[logv["_d"].apply(lambda d: (d is not None) and (d <= dt_max))]
                    logv = logv.drop(columns=["_d"], errors="ignore")

                st.caption(f"{len(logv)} evento(s) filtrados)")

                total = len(logv)
                if total > 0:
                    pcols = st.columns([1, 3, 1])
                    with pcols[0]:
                        page = st.number_input("Página", min_value=1, max_value=max(1, (total - 1) // page_size + 1), value=1, step=1)
                    start = (int(page) - 1) * int(page_size); end = start + int(page_size)
                    view = logv.iloc[start:end].copy()
                else:
                    view = logv.copy()
                st.dataframe(view, use_container_width=True)

                try:
                    dts = pd.to_datetime(logv["ts"].str.replace("Z", "", regex=False), errors="coerce").dropna()
                    if not dts.empty:
                        pmin = dts.min().strftime("%Y-%m-%d"); pmax = dts.max().strftime("%Y-%m-%d")
                        periodo = f"{pmin}_{pmax}" if pmin != pmax else pmin
                    else:
                        periodo = datetime.utcnow().strftime("%Y-%m-%d")
                except Exception:
                    periodo = datetime.utcnow().strftime("%Y-%m-%d")
                usuario_lbl = s.get("username") or "anon"

                cdl1, cdl2 = st.columns([1, 1])
                with cdl1:
                    st.download_button(
                        "⬇️ CSV (filtro aplicado)",
                        data=logv.to_csv(index=False).encode("utf-8"),
                        file_name=f"audit_{periodo}_{usuario_lbl}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                with cdl2:
                    st.download_button(
                        "⬇️ JSONL (completo)",
                        data=AUDIT_LOG.read_bytes() if AUDIT_LOG.exists() else b"",
                        file_name=f"audit_full_{periodo}.jsonl",
                        mime="application/json",
                        use_container_width=True,
                    )

# =============================================================================
# Cabeçalho técnico: material e norma aplicável
# =============================================================================
def _norma_por_material(material: str) -> str:
    material = (material or "").strip().lower()

    if material == "concreto":
        return "NBR 5739 - Ensaio de Compressão de Corpos de Prova Cilíndrico"

    if material == "argamassa":
        return "NBR 13279 - Argamassa para assentamento e revestimento de paredes e tetos"

    if material == "graute":
        return "NBR 5739 - Ensaio de Compressão de Corpos de Prova Cilíndricos - Graute"

    return "Norma técnica não informada"


def _dimensao_cp_por_material(material: str) -> str:
    material = (material or "").strip().lower()

    if material in ("concreto", "graute"):
        return "Corpo de prova cilíndrico 10 cm x 20 cm"

    if material == "argamassa":
        return "Corpo de prova prismático 4,00 cm x 4,00 cm"

    return "Dimensão do corpo de prova não informada"


def _normalizar_material(material: str) -> str:
    t = (material or "").strip().lower()
    if "argamassa" in t:
        return "Argamassa"
    if "graute" in t or "garute" in t:
        return "Graute"
    if "concreto" in t:
        return "Concreto"
    return "Concreto"


def _inferir_material_certificado(cp: str = "", norma_texto: str = "", local_texto: str = "", fallback: str = "Concreto") -> str:
    """Identifica o material por linha/relatório.

    Regras principais:
    - CP iniciado por A (ex.: A562) = Argamassa.
    - Texto/norma com graute/garute/grauteamento = Graute.
    - CP numérico (ex.: 045.172) = Concreto, exceto quando o bloco/local indicar Graute.
    - Norma NBR 13279 ou texto de argamassa = Argamassa quando não houver CP numérico.
    - Demais casos = Concreto.
    """
    cp_s = str(cp or "").strip().upper()
    norma_s = str(norma_texto or "").lower()
    local_s = str(local_texto or "").lower()
    base_s = f"{norma_s} {local_s}"

    # CPs de argamassa na base Habisolute normalmente começam com A: A562, A039.258 etc.
    if re.fullmatch(r"A\d+(?:\.\d+)?", cp_s):
        return "Argamassa"

    # Graute tem prioridade sobre concreto quando o próprio bloco/local indicar grauteamento.
    if "graute" in base_s or "garute" in base_s or "grauteamento" in base_s or "garuteamento" in base_s:
        return "Graute"

    # CP numérico é tratado como concreto, salvo regra de graute acima.
    if re.fullmatch(r"\d{3,6}(?:\.\d{3})?", cp_s):
        return "Concreto"

    # Sem CP numérico, usa a norma/texto do bloco.
    if "13279" in base_s or "argamassa" in base_s:
        return "Argamassa"

    return _normalizar_material(fallback)


def _atualizar_material_norma_linhas(df_: pd.DataFrame) -> pd.DataFrame:
    """Recalcula Material/Norma/Corpo de Prova linha a linha após filtros.

    Isso garante que, ao mudar o fck para análise, a norma exibida acompanhe
    o grupo realmente selecionado, mesmo quando o certificado tiver Argamassa,
    Concreto e Graute no mesmo PDF.
    """
    if df_ is None or df_.empty:
        return df_
    df_ = df_.copy()
    materiais = []
    for _, row in df_.iterrows():
        cp = row.get("CP", "")
        local = row.get("Local", "")
        norma_atual = row.get("Norma Técnica", "")
        material_atual = row.get("Material", s.get("rt_material", "Concreto"))
        mat = _inferir_material_certificado(cp, norma_atual, local, material_atual)
        materiais.append(mat)
    df_["Material"] = materiais
    df_["Norma Técnica"] = [_norma_por_material(m) for m in materiais]
    df_["Corpo de Prova"] = [_dimensao_cp_por_material(m) for m in materiais]
    return df_


def _resumo_material_norma_df(df_: pd.DataFrame) -> tuple[str, str, str]:
    """Monta resumo de material/norma/corpo de prova para tela e PDF.
    Quando houver materiais mistos, mostra todos os identificados.
    """
    if df_ is None or df_.empty or "Material" not in df_.columns:
        material = s.get("rt_material", "Concreto")
        return material, _norma_por_material(material), _dimensao_cp_por_material(material)

    materiais = []
    for m in df_["Material"].dropna().astype(str).tolist():
        nm = _normalizar_material(m)
        if nm not in materiais:
            materiais.append(nm)
    if not materiais:
        materiais = [s.get("rt_material", "Concreto")]

    material_label = " / ".join(materiais)
    norma_label = "<br/>".join([_norma_por_material(m) for m in materiais])
    dimensao_label = "<br/>".join([f"{m}: {_dimensao_cp_por_material(m)}" for m in materiais])
    return material_label, norma_label, dimensao_label


def _fmt_data_calibracao(valor: Any) -> str:
    """Formata data de validade da calibração para dd/mm/aaaa quando possível."""
    if valor is None:
        return ""
    txt = str(valor).strip()
    if not txt or txt.lower() in ("none", "nan", "nat"):
        return ""
    try:
        dt = pd.to_datetime(valor, errors="coerce", dayfirst=True)
        if pd.notna(dt):
            return dt.strftime("%d/%m/%Y")
    except Exception:
        pass
    return txt


def _dados_calibracao_por_material(material: str) -> str:
    """Retorna texto de calibração da prensa conforme o material.

    Concreto e Graute usam a prensa de concreto/graute.
    Argamassa usa a prensa de argamassa.
    """
    mat = _normalizar_material(material)
    if mat == "Argamassa":
        nome = s.get("cal_prensa_argamassa_nome", "")
        cert = s.get("cal_prensa_argamassa_cert", "")
        validade = s.get("cal_prensa_argamassa_validade", "")
    else:
        nome = s.get("cal_prensa_concreto_nome", "")
        cert = s.get("cal_prensa_concreto_cert", "")
        validade = s.get("cal_prensa_concreto_validade", "")

    nome = str(nome or "").strip()
    cert = str(cert or "").strip()
    validade = _fmt_data_calibracao(validade)

    partes = []
    if nome:
        partes.append(f"Prensa: {nome}")
    if cert:
        partes.append(f"Certificado: {cert}")
    if validade:
        partes.append(f"Validade: {validade}")

    return " | ".join(partes) if partes else "Calibração da prensa não informada"


def _resumo_calibracao_df(df_: pd.DataFrame) -> str:
    """Monta resumo da calibração por material presente no grupo filtrado."""
    if df_ is None or df_.empty or "Material" not in df_.columns:
        mat = _normalizar_material(s.get("rt_material", "Concreto"))
        return f"{mat} — {_dados_calibracao_por_material(mat)}"

    materiais = []
    for m in df_["Material"].dropna().astype(str).tolist():
        nm = _normalizar_material(m)
        if nm not in materiais:
            materiais.append(nm)
    if not materiais:
        materiais = [_normalizar_material(s.get("rt_material", "Concreto"))]

    return "<br/>".join([f"{m} — {_dados_calibracao_por_material(m)}" for m in materiais])

# =============================================================================
# Sidebar
# =============================================================================
with st.sidebar:
    st.markdown("""
    <div class="sidebar-brand">
      <div class="sidebar-brand-title">H<span>ABI</span>SOLUTE</div>
      <div class="sidebar-brand-sub">PAINEL DE CONTROLE</div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("### ⚙️ Configurações")
    s["wide_layout"] = st.toggle("Tela larga (1800px)", value=bool(s.get("wide_layout", True)), key="opt_wide_layout")
    s["BATCH_MODE"] = st.toggle("Modo Lote (vários PDFs)", value=bool(s["BATCH_MODE"]), key="opt_batch_mode")
    if s["BATCH_MODE"] != s["_prev_batch"]:
        s["_prev_batch"] = s["BATCH_MODE"]
        s["uploader_key"] += 1
    s["TOL_MP"] = st.slider("Tolerância Real × Estimado (MPa)", 0.0, 5.0, float(s["TOL_MP"]), 0.1, key="opt_tol_mpa")
    st.markdown("---")
    st.markdown("#### 📄 Dados do relatório")
    materiais_opts = ["Concreto", "Argamassa", "Graute"]
    material_atual = s.get("rt_material", "Concreto")
    if material_atual not in materiais_opts:
        material_atual = "Concreto"
    s["rt_material"] = st.selectbox(
        "Material",
        materiais_opts,
        index=materiais_opts.index(material_atual),
        help="Define automaticamente a norma técnica aplicada no cabeçalho do relatório."
    )
    st.markdown(
        f"""
        <div style='text-align:center; padding:8px 10px; border-radius:10px; border:1px solid var(--line); background:rgba(249,115,22,.08); font-size:12px; line-height:1.35;'>
            <b>Norma aplicada</b><br>{_norma_por_material(s['rt_material'])}<br>
            <b>Corpo de prova</b><br>{_dimensao_cp_por_material(s['rt_material'])}
        </div>
        """,
        unsafe_allow_html=True
    )
    s["rt_responsavel"] = st.text_input("Responsável técnico", value=s.get("rt_responsavel",""))
    s["rt_cliente"]     = st.text_input("Cliente / Empreendimento", value=s.get("rt_cliente",""))
    s["rt_cidade"]      = st.text_input("Cidade / UF", value=s.get("rt_cidade",""))

    st.markdown("---")
    st.markdown("#### 🧪 Calibração das prensas")
    with st.expander("Prensa de Concreto / Graute", expanded=False):
        s["cal_prensa_concreto_nome"] = st.text_input(
            "Nome da prensa - Concreto/Graute",
            value=s.get("cal_prensa_concreto_nome", ""),
            placeholder="Ex.: P01 - 4HCI 100 tf"
        )
        s["cal_prensa_concreto_cert"] = st.text_input(
            "Nº do certificado - Concreto/Graute",
            value=s.get("cal_prensa_concreto_cert", ""),
            placeholder="Ex.: ACTEST 04114A26"
        )
        s["cal_prensa_concreto_validade"] = st.date_input(
            "Validade da calibração - Concreto/Graute",
            value=None,
            key="cal_prensa_concreto_validade_input"
        )

    with st.expander("Prensa de Argamassa", expanded=False):
        s["cal_prensa_argamassa_nome"] = st.text_input(
            "Nome da prensa - Argamassa",
            value=s.get("cal_prensa_argamassa_nome", ""),
            placeholder="Ex.: Prensa Argamassa"
        )
        s["cal_prensa_argamassa_cert"] = st.text_input(
            "Nº do certificado - Argamassa",
            value=s.get("cal_prensa_argamassa_cert", ""),
            placeholder="Ex.: Certificado nº ..."
        )
        s["cal_prensa_argamassa_validade"] = st.date_input(
            "Validade da calibração - Argamassa",
            value=None,
            key="cal_prensa_argamassa_validade_input"
        )

    st.markdown("---")
    st.caption(f"Usuário: **{nome_login}** ({papel})")

# =============================================================================
# Utilidades de parsing / limpeza
# =============================================================================
def _limpa_horas(txt: str) -> str:
    txt = re.sub(r"\b\d{1,2}:\d{2}\b", "", txt)
    txt = re.sub(r"\bàs\s*\d{1,2}:\d{2}\b", "", txt, flags=re.I)
    return re.sub(r"\s{2,}", " ", txt).strip(" -•:;,.") 

def _limpa_usina_extra(txt: Optional[str]) -> Optional[str]:
    if not txt: return txt
    t = _limpa_horas(str(txt))
    t = re.sub(r"(?i)relat[óo]rio:\s*\d+\s*", "", t)
    t = re.sub(r"(?i)\busina:\s*", "", t)
    t = re.sub(r"(?i)\bsa[ií]da\s+da\s+usina\b.*$", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -•:;,.")
    return t or None

def _detecta_usina(linhas: List[str]) -> Optional[str]:
    for sline in linhas:
        if re.search(r"(?i)\busina:", sline):
            s0 = _limpa_horas(sline)
            m = re.search(r"(?i)usina:\s*([A-Za-zÀ-ÿ0-9 .\-]+?)(?:\s+sa[ií]da\s+da\s+usina\b|$)", s0)
            if m: return _limpa_usina_extra(m.group(1)) or _limpa_usina_extra(m.group(0))
            return _limpa_usina_extra(s0)
    for sline in linhas:
        if re.search(r"(?i)\busina\b", sline) or re.search(r"(?i)sa[ií]da da usina", sline):
            t = _limpa_horas(sline)
            t2 = re.sub(r"(?i)^.*\busina\b[:\-]?\s*", "", t).strip()
            if t2: return t2
            if t: return t
    return None

def _parse_abatim_nf_pair(tok: str) -> Tuple[Optional[float], Optional[float]]:
    if not tok: return None, None
    t = str(tok).strip().lower().replace("±", "+-").replace("mm", "").replace(",", ".").replace(" ", "")
    m = re.match(r"^\s*(\d+(?:\.\d+)?)(?:\s*\+?-?\s*(\d+(?:\.\d+)?))?\s*$", t)
    if not m: return None, None
    try:
        v = float(m.group(1))
        tol = float(m.group(2)) if m.group(2) is not None else None
        return v, tol
    except Exception:
        return None, None

def _detecta_abatimentos(linhas: List[str]) -> Tuple[Optional[float], Optional[float]]:
    abat_nf = None; abat_obra = None
    for sline in linhas:
        s_clean = sline.replace(",", ".").replace("±", "+-")
        m_nf = re.search(
            r"(?i)abat(?:imento|\.?im\.?)\s*(?:de\s*)?nf[^0-9]*"
            r"(\d+(?:\.\d+)?)(?:\s*\+?-?\s*\d+(?:\.\d+)?)?\s*mm?",
            s_clean
        )
        if m_nf and abat_nf is None:
            try: abat_nf = float(m_nf.group(1))
            except Exception: pass
        m_obra = re.search(
            r"(?i)abat(?:imento|\.?im\.?).*(obra|medido em obra)[^0-9]*"
            r"(\d+(?:\.\d+)?)\s*mm",
            s_clean
        )
        if m_obra and abat_obra is None:
            try: abat_obra = float(m_obra.group(2))
            except Exception: pass
    return abat_nf, abat_obra

def _extract_fck_values(line: str) -> List[float]:
    if not line or "fck" not in line.lower(): return []
    sanitized = line.replace(",", ".")
    parts = re.split(r"(?i)fck", sanitized)[1:]
    if not parts: return []
    values: List[float] = []
    age_with_suffix = re.compile(r"^(\d{1,3})(?:\s*(?:dias?|d))\b\s*[:=]?", re.I)
    age_plain       = re.compile(r"^(\d{1,3})\b\s*[:=]?", re.I)
    age_tokens = {1, 3, 7, 14, 21, 28, 56, 63, 90}
    cut_keywords = ("mpa","abatimento","slump","nota","usina","relatório","relatorio","consumo","traço","traco","cimento","dosagem")
    for segment in parts:
        starts_immediate = bool(segment) and not segment[0].isspace()
        seg = segment.lstrip(" :=;-()[]")
        changed = True
        while changed:
            changed = False
            m = age_with_suffix.match(seg)
            if m:
                age_val = int(m.group(1))
                if age_val in age_tokens:
                    seg = seg[m.end():].lstrip(" :=;-()[]"); changed = True; continue
            if starts_immediate:
                m2 = age_plain.match(seg)
                if m2:
                    age_val = int(m2.group(1))
                    if age_val in age_tokens:
                        seg = seg[m2.end():].lstrip(" :=;-()[]"); changed = True; continue
        lower_seg = seg.lower()
        cut_at = len(seg)
        for kw in cut_keywords:
            idx = lower_seg.find(kw)
            if idx != -1: cut_at = min(cut_at, idx)
        seg = seg[:cut_at]
        for num in re.findall(r"\d+(?:\.\d+)?", seg):
            try: val = float(num)
            except ValueError: continue
            if 3 <= val <= 120 and val not in values:
                values.append(val)
    return values

def _to_float_or_none(value: Any) -> Optional[float]:
    try: val = float(value)
    except (TypeError, ValueError): return None
    return None if pd.isna(val) else val

def _format_float_label(value: Optional[float]) -> str:
    if value is None or pd.isna(value): return "—"
    num = float(value)
    label = f"{num:.2f}".rstrip("0").rstrip(".")
    return label or f"{num:.2f}"

def _normalize_fck_label(value: Any) -> str:
    normalized = _to_float_or_none(value)
    if normalized is not None: return _format_float_label(normalized)
    raw = str(value).strip()
    if not raw or raw.lower() == 'nan': return "—"
    return raw

def extrair_dados_certificado(uploaded_file):
    # mesmo do teu, já preparado para pegar idades variadas
    try:
        raw = uploaded_file.read()
        uploaded_file.seek(0)
    except Exception:
        raw = uploaded_file.getvalue()

    linhas_todas = []
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page in pdf.pages:
                txt = page.extract_text() or ""
                txt = re.sub(r"[“”]", "\"", txt)
                txt = re.sub(r"[’´`]", "'", txt)
                linhas_todas.extend([l.strip() for l in txt.split("\n") if l.strip() ])
    except Exception:
        return (pd.DataFrame(columns=[
            "Relatório","CP","Idade (dias)","Resistência (MPa)","Nota Fiscal","Local",
            "Usina","Abatimento NF (mm)","Abatimento NF tol (mm)","Abatimento Obra (mm)",
            "Material","Norma Técnica","Corpo de Prova"
        ]), "NÃO IDENTIFICADA", "NÃO IDENTIFICADA", "NÃO IDENTIFICADO")

    cp_regex = re.compile(r"^(?:[A-Z]{0,2})?\d{3,6}(?:\.\d{3})?$", re.I)
    data_regex = re.compile(r"\d{2}/\d{2}/\d{4}")
    data_token = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    tipo_token = re.compile(r"^A\d$", re.I)
    float_token = re.compile(r"^\d+[.,]\d+$")

    # NOTA FISCAL — aceita números com separadores e combinações alfa-numéricas
    # Exemplos: NA, AB0236, 001, 1236, 1.236, 12.369, 131,711, 25.969.789, etc.
    def _clean_nf_token(t: str) -> str:
        if t is None:
            return ""
        t0 = str(t).strip()
        # remove pontuação periférica, mantendo separadores internos (.,-,/)
        t0 = t0.strip(" \t\r\n,;:()[]{}<>")
        # NF pode vir com vírgula como separador no PDF, ex.: 131,711.
        # Para não confundir com número decimal, normalizamos como separador interno de NF.
        if re.fullmatch(r"\d{1,3},\d{3}(?:,\d{3})*", t0):
            t0 = t0.replace(",", ".")
        return t0

    def _is_nf_token(tok: str, cp_val: str, relatorio: str = "") -> bool:
        """Heurística para reconhecer o token de Nota Fiscal (NF).

        Aceita números (com ou sem separador de milhar '.'), alfanuméricos (ex.: H682, A039.258) e variações comuns.
        Rejeita: o próprio CP, o número do relatório, idades/betoneira (1-2 dígitos) e tokens vazios.
        """
        tok = (tok or "").strip()
        if not tok:
            return False
        if cp_val and tok.strip().upper() == str(cp_val).strip().upper():
            return False
        if relatorio and tok == relatorio:
            return False

        t = tok.strip().upper()
        if re.fullmatch(r"\d{1,3},\d{3}(?:,\d{3})*", t):
            t = t.replace(",", ".")

        # 1-2 dígitos normalmente são betoneira/idade
        if re.fullmatch(r"\d{1,2}", t):
            return False

        # somente caracteres esperados
        if re.fullmatch(r"[A-Z0-9][A-Z0-9.,\-/]{0,24}", t) is None:
            return False

        # só números (>=3 dígitos)
        if re.fullmatch(r"\d{3,12}", t):
            return True

        # com separador de milhar (037.421, 1.236, 25.969.789)
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", t):
            return True

        # alfanumérico (H682, A039.258)
        if re.fullmatch(r"[A-Z]+\d+(?:\.\d+)*", t):
            return True

        return True

    pecas_regex = re.compile(r"(?i)peç[ac]s?\s+concretad[ao]s?:\s*(.*)")

    obra = "NÃO IDENTIFICADA"
    data_relatorio = "NÃO IDENTIFICADA"
    fck_projeto = "NÃO IDENTIFICADO"
    local_por_relatorio: Dict[str, str] = {}
    relatorio_atual = None
    fck_por_relatorio: Dict[str, List[float]] = {}
    fck_valores_globais: List[float] = []
    material_por_relatorio: Dict[str, str] = {}
    norma_por_relatorio: Dict[str, str] = {}
    corpo_por_relatorio: Dict[str, str] = {}
    usina_por_relatorio: Dict[str, str] = {}
    norma_contexto = ""
    material_contexto = s.get("rt_material", "Concreto")

    for sline in linhas_todas:
        if sline.startswith("Obra:"):
            obra = sline.replace("Obra:", "").strip().split(" Data")[0]
        m_data = data_regex.search(sline)
        if m_data and data_relatorio == "NÃO IDENTIFICADA":
            data_relatorio = m_data.group()
        if re.search(r"(?i)Norma\s+NBR", sline):
            norma_contexto = sline.strip()
            material_contexto = _inferir_material_certificado("", norma_contexto, "", material_contexto)
        if sline.startswith("Relatório:"):
            m_rel = re.search(r"Relatório:\s*(\d+)", sline)
            if m_rel:
                relatorio_atual = m_rel.group(1)
                mat_rel = _inferir_material_certificado("", norma_contexto, "", material_contexto)
                material_por_relatorio[relatorio_atual] = mat_rel
                norma_por_relatorio[relatorio_atual] = _norma_por_material(mat_rel)
                corpo_por_relatorio[relatorio_atual] = _dimensao_cp_por_material(mat_rel)
                m_us = re.search(r"(?i)usina:\s*([A-Za-zÀ-ÿ0-9 .\-]+?)(?:\s+sa[ií]da\s+da\s+usina\b|$)", sline)
                if m_us:
                    usina_por_relatorio[relatorio_atual] = _limpa_usina_extra(m_us.group(1)) or _limpa_usina_extra(m_us.group(0))
        m_pecas = pecas_regex.search(sline)
        if m_pecas and relatorio_atual:
            local_txt = m_pecas.group(1).strip().rstrip(".")
            local_por_relatorio[relatorio_atual] = local_txt
            mat_rel = _inferir_material_certificado("", norma_por_relatorio.get(relatorio_atual, norma_contexto), local_txt, material_por_relatorio.get(relatorio_atual, material_contexto))
            material_por_relatorio[relatorio_atual] = mat_rel
            norma_por_relatorio[relatorio_atual] = _norma_por_material(mat_rel)
            corpo_por_relatorio[relatorio_atual] = _dimensao_cp_por_material(mat_rel)
        if "fck" in sline.lower():
            valores_fck = _extract_fck_values(sline)
            if valores_fck:
                if relatorio_atual:
                    fck_por_relatorio.setdefault(relatorio_atual, []).extend(valores_fck)
                else:
                    fck_valores_globais.extend(valores_fck)
                if not isinstance(fck_projeto, (int, float)):
                    try: fck_projeto = float(valores_fck[0])
                    except Exception: pass

    usina_nome = _limpa_usina_extra(_detecta_usina(linhas_todas))
    abat_nf_pdf, abat_obra_pdf = _detecta_abatimentos(linhas_todas)

    dados = []
    relatorio_cabecalho = None

    for sline in linhas_todas:
        partes = sline.split()

        if sline.startswith("Relatório:"):
            m_rel = re.search(r"Relatório:\s*(\d+)", sline)
            if m_rel: relatorio_cabecalho = m_rel.group(1)
            continue

        if len(partes) >= 5 and cp_regex.match(partes[0]):
            try:
                cp = partes[0]
                relatorio = relatorio_cabecalho or "NÃO IDENTIFICADO"

                i_data = next((i for i, t in enumerate(partes) if data_token.match(t)), None)
                if i_data is not None:
                    i_tipo = next((i for i in range(i_data + 1, len(partes)) if tipo_token.match(partes[i])), None)
                    start = (i_tipo + 1) if i_tipo is not None else (i_data + 1)
                else:
                    start = 1

                idade_idx, idade = None, None
                for j in range(start, len(partes)):
                    t = partes[j]
                    if t.isdigit():
                        v = int(t)
                        if 1 <= v <= 120:
                            idade = v; idade_idx = j; break

                resistência, res_idx = None, None
                if idade_idx is not None:
                    for j in range(idade_idx + 1, len(partes)):
                        t = partes[j]
                        if float_token.match(t):
                            resistência = float(t.replace(",", "."))
                            res_idx = j; break

                if idade is None or resistência is None:
                    continue

                nf, nf_idx = None, None
                start_nf = (res_idx + 1) if res_idx is not None else (idade_idx + 1)
                for j in range(start_nf, len(partes)):
                    tok = partes[j]
                    tok_nf = _clean_nf_token(tok)
                    if _is_nf_token(tok_nf, cp):
                        nf = tok_nf
                        nf_idx = j
                        break

                abat_obra_val = None
                if i_data is not None:
                    for j in range(i_data - 1, max(-1, i_data - 6), -1):
                        tok = partes[j]
                        if re.fullmatch(r"\d{2,3}", tok):
                            v = int(tok)
                            if 20 <= v <= 400:
                                abat_obra_val = float(v); break

                abat_nf_val, abat_nf_tol = None, None
                if nf_idx is not None:
                    for tok in partes[nf_idx + 1: nf_idx + 5]:
                        v, tol = _parse_abatim_nf_pair(tok)
                        if v is not None and 20 <= v <= 400:
                            abat_nf_val = float(v)
                            abat_nf_tol = float(tol) if tol is not None else None
                            break

                local = local_por_relatorio.get(relatorio)
                material_linha = _inferir_material_certificado(
                    cp,
                    norma_por_relatorio.get(relatorio, norma_contexto),
                    local,
                    material_por_relatorio.get(relatorio, s.get("rt_material", "Concreto"))
                )
                norma_linha = _norma_por_material(material_linha)
                corpo_linha = _dimensao_cp_por_material(material_linha)
                usina_linha = usina_por_relatorio.get(relatorio, usina_nome)
                dados.append([
                    relatorio, cp, idade, resistência, (nf if nf else relatorio), local,
                    usina_linha,
                    (abat_nf_val if abat_nf_val is not None else abat_nf_pdf),
                    abat_nf_tol,
                    (abat_obra_val if abat_obra_val is not None else abat_obra_pdf),
                    material_linha, norma_linha, corpo_linha
                ])
            except Exception:
                pass

    df = pd.DataFrame(dados, columns=[
        "Relatório","CP","Idade (dias)","Resistência (MPa)","Nota Fiscal","Local",
        "Usina","Abatimento NF (mm)","Abatimento NF tol (mm)","Abatimento Obra (mm)",
        "Material","Norma Técnica","Corpo de Prova"
    ])

    if not df.empty:
        rel_map = {}
        for rel, valores in fck_por_relatorio.items():
            uniques = []
            for valor in valores:
                try: val_f = float(valor)
                except Exception: continue
                if val_f not in uniques: uniques.append(val_f)
            if uniques: rel_map[rel] = uniques[0]

        fallback_fck = None
        if isinstance(fck_projeto, (int, float)):
            fallback_fck = float(fck_projeto)
        else:
            candidatos = []
            for valores in fck_por_relatorio.values(): candidatos.extend(valores)
            candidatos.extend(fck_valores_globais)
            for cand in candidatos:
                try:
                    fallback_fck = float(cand); break
                except Exception:
                    continue

        if rel_map or fallback_fck is not None:
            df["Relatório"] = df["Relatório"].astype(str)
            df["Fck Projeto"] = df["Relatório"].map(rel_map)
            if fallback_fck is not None:
                df["Fck Projeto"] = df["Fck Projeto"].fillna(fallback_fck)

    return df, obra, data_relatorio, fck_projeto

# =============================================================================
# KPIs e utilidades
# =============================================================================
def compute_exec_kpis(df_view: pd.DataFrame, fck_val: Optional[float]):
    def _pct_hit(age):
        if fck_val is None or pd.isna(fck_val): return None
        sub = df_view[df_view["Idade (dias)"] == age]
        # Regra Habisolute: se pelo menos 1 corpo de prova do par atingir o fck,
        # o CP é considerado aprovado naquela idade.
        g = sub.groupby("CP")["Resistência (MPa)"].max()
        if g.empty: return None
        return float((g >= fck_val).mean() * 100.0)
    pct28 = _pct_hit(28)
    pct63 = _pct_hit(63)
    media_geral = float(pd.to_numeric(df_view["Resistência (MPa)"], errors="coerce").mean()) if not df_view.empty else None
    dp_geral   = float(pd.to_numeric(df_view["Resistência (MPa)"], errors="coerce").std())  if not df_view.empty else None
    n_rel      = df_view["Relatório"].nunique()
    def _semaforo(p28, p63):
        if (p28 is None) and (p63 is None): return ("Sem dados", "#9ca3af")
        score = 0.0
        if p28 is not None: score += float(p28) * 0.6
        if p63 is not None: score += float(p63) * 0.4
        if score >= 90: return ("✅ Bom", "#16a34a")
        if score >= 75: return ("⚠️ Atenção", "#d97706")
        return ("🔴 Crítico", "#ef4444")
    status_txt, status_cor = _semaforo(pct28, pct63)
    return {"pct28": pct28, "pct63": pct63, "media": media_geral, "dp": dp_geral, "n_rel": n_rel, "status_txt": status_txt, "status_cor": status_cor}

def place_right_legend(ax):
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), loc="upper left", bbox_to_anchor=(1.02, 1.0),
              frameon=False, ncol=1, handlelength=2.2, handletextpad=0.8, labelspacing=0.35, prop={"size": 9})
    plt.subplots_adjust(right=0.80)


def render_fck_dashboard(pv_df: pd.DataFrame, fck_value: Optional[float]):
    """Painel visual premium da verificação do FCK sem alterar a lógica técnica."""
    import html as _html
    import re as _re

    if pv_df is None or pv_df.empty:
        st.info("Sem dados para montar o painel de verificação do FCK.")
        return

    dfp = pv_df.copy()

    # Alerta de consistência entre resultados do mesmo par.
    # Importante: este alerta NÃO altera a regra de aprovação do FCK;
    # ele serve para chamar a atenção para diferenças internas > 2,0 MPa.
    pair_alert_col = next(
        (c for c in dfp.columns if "Alerta Pares" in str(c) or "Δ>2 MPa" in str(c)),
        None
    )

    def _has_pair_alert(value) -> bool:
        txt = str(value or "").lower()
        return bool(txt.strip()) and (("2 mpa" in txt) or ("Δ" in str(value)) or ("delta" in txt))

    pair_alert_flags = [
        _has_pair_alert(r.get(pair_alert_col, "")) if pair_alert_col else False
        for _, r in dfp.iterrows()
    ]
    pair_alert_count = sum(pair_alert_flags)
    pair_alert_color = "#ff6b7d" if pair_alert_count else "#65e887"

    def _mp_cols(age):
        cols = []
        for c in dfp.columns:
            if _re.match(rf"^{age}d(?:\s+#\d+)?\s+\(MPa\)$", str(c)):
                cols.append(c)
        def _rep_key(col):
            m = _re.search(r"#(\d+)", str(col))
            return int(m.group(1)) if m else 1
        return sorted(cols, key=_rep_key)

    ages = []
    for age in (1, 3, 7, 14, 21, 28, 56, 63):
        cols = _mp_cols(age)
        if cols and any(pd.to_numeric(dfp[c], errors="coerce").notna().any() for c in cols):
            ages.append(age)

    def _status_col(age):
        c = f"Status {age}d"
        return c if c in dfp.columns else None

    def _status_kind(txt):
        t = str(txt or "").lower()
        if "não atingiu" in t or "nao atingiu" in t:
            return "bad"
        if "atingiu" in t:
            return "ok"
        if "coletando" in t or "análise" in t or "analise" in t:
            return "wait"
        return "none"

    def _clean_status(txt):
        t = str(txt or "")
        for symbol in ("🟢", "🔴", "🟡", "⚪", "🟠", "✅", "⚠️"):
            t = t.replace(symbol, "")
        return t.strip() or "Sem dados"

    def _fmt(v):
        try:
            if pd.isna(v):
                return "—"
            return f"{float(v):.2f}".replace(".", ",")
        except Exception:
            return "—"

    # Situação final: usa a idade final mais avançada disponível para cada CP.
    final_states = []
    for _, r in dfp.iterrows():
        state = "wait"
        for age in (63, 56, 28):
            vals = pd.to_numeric(pd.Series([r.get(c) for c in _mp_cols(age)]), errors="coerce").dropna()
            if not vals.empty:
                sc = _status_col(age)
                state = _status_kind(r.get(sc, "")) if sc else "none"
                break
        final_states.append(state)

    total = len(dfp)
    ok_count = sum(x == "ok" for x in final_states)
    bad_count = sum(x == "bad" for x in final_states)
    wait_count = total - ok_count - bad_count
    ok_pct = (100.0 * ok_count / total) if total else 0.0
    fck_txt = "—" if fck_value is None or pd.isna(fck_value) else f"{float(fck_value):.0f} MPa"

    preferred_final = next((a for a in (63, 56, 28) if a in ages), None)
    max_final = None
    max_cp = "—"
    media_bests = []
    for _, r in dfp.iterrows():
        cp = str(r.get("CP", "—"))
        vals_all = []
        for age in (28, 56, 63):
            for c in _mp_cols(age):
                vv = pd.to_numeric(pd.Series([r.get(c)]), errors="coerce").dropna()
                if not vv.empty:
                    vals_all.append(float(vv.iloc[0]))
        if vals_all:
            m = max(vals_all)
            if max_final is None or m > max_final:
                max_final = m
                max_cp = cp
        if preferred_final is not None:
            vals_pref = pd.to_numeric(pd.Series([r.get(c) for c in _mp_cols(preferred_final)]), errors="coerce").dropna()
            if not vals_pref.empty:
                media_bests.append(float(vals_pref.max()))

    media_final = (sum(media_bests) / len(media_bests)) if media_bests else None
    max_final_txt = "—" if max_final is None else f"{max_final:.2f} MPa".replace(".", ",")
    media_final_txt = "—" if media_final is None else f"{media_final:.2f} MPa".replace(".", ",")
    age_final_txt = "—" if preferred_final is None else f"{preferred_final} dias"

    css = """
    <style>
      .hf-wrap{--line:#21364a;--muted:#91a4b9;background:linear-gradient(145deg,#06101a,#091522 55%,#07111d);
        border:1px solid #1d3042;border-radius:20px;padding:16px;box-shadow:0 18px 48px rgba(0,0,0,.22);margin:6px 0 14px;overflow:hidden}
      .hf-top{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;padding:4px 6px 14px}
      .hf-title{font-size:24px;font-weight:900;color:#f8fafc;letter-spacing:-.3px}.hf-sub{font-size:13px;color:#aebed0;margin-top:4px}
      .hf-rule{font-size:12px;color:#cbd5e1;background:#101f30;border:1px solid #263b50;border-radius:999px;padding:7px 11px;white-space:nowrap}.hf-rule b{color:#fb923c}
      .hf-kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:14px}
      .hf-kpi{background:linear-gradient(180deg,#0e1c2c,#0a1623);border:1px solid #22364a;border-radius:14px;padding:13px 14px;min-height:74px}
      .hf-kl{font-size:11px;color:#9fb0c3;margin-bottom:7px}.hf-kv{font-size:23px;color:#fff;font-weight:900;line-height:1}.hf-ks{font-size:11px;color:#73869b;margin-top:6px}
      .hf-box{overflow-x:auto;border:1px solid #203448;border-radius:14px;background:#07111b}.hf-table{border-collapse:separate;border-spacing:0;width:100%;min-width:1080px;color:#e5edf6;font-size:12px}
      .hf-table th,.hf-table td{border-right:1px solid #203448;border-bottom:1px solid #203448;padding:9px 10px;text-align:center;vertical-align:middle}.hf-table th:last-child,.hf-table td:last-child{border-right:0}.hf-table tr:last-child td{border-bottom:0}
      .hf-table thead tr:first-child th{background:#122235;color:#f8fafc;font-weight:850}.hf-table thead tr:nth-child(2) th{background:#0c1a29;color:#aebed0;font-size:11px}
      .hf-table tbody tr{background:#08131f}.hf-table tbody tr:nth-child(even){background:#0a1622}.hf-table tbody tr:hover{background:#0e1d2c}.hf-cp{font-weight:900;font-size:13px;color:#fff;text-align:left!important;white-space:nowrap}.hf-val{font-weight:800;color:#f8fafc;white-space:nowrap}
      .hf-badge{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:5px 8px;font-weight:850;white-space:nowrap;border:1px solid transparent}.hf-dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:currentColor;box-shadow:0 0 10px currentColor}
      .hf-ok{color:#62e887;background:rgba(34,197,94,.10);border-color:rgba(74,222,128,.22)}.hf-bad{color:#ff6374;background:rgba(244,63,94,.11);border-color:rgba(251,75,95,.22)}.hf-wait{color:#ffd54a;background:rgba(250,204,21,.09);border-color:rgba(250,204,21,.18)}.hf-none{color:#d8d4ff;background:rgba(196,181,253,.08);border-color:rgba(196,181,253,.16)}
      .hf-final{min-width:126px;justify-content:center;border-radius:8px}.hf-final-ok{color:#70f08e;background:rgba(34,197,94,.18);border:1px solid rgba(74,222,128,.28)}.hf-final-bad{color:#ff6b7d;background:rgba(244,63,94,.18);border:1px solid rgba(251,75,95,.28)}.hf-final-wait{color:#ffd95e;background:rgba(245,158,11,.17);border:1px solid rgba(250,204,21,.24)}
      .hf-foot{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:12px}.hf-mini{background:#0a1724;border:1px solid #203448;border-radius:12px;padding:11px 13px}.hf-ml{font-size:10.5px;color:#8ea2b8}.hf-mv{font-size:18px;font-weight:900;color:#f8fafc;margin-top:4px}.hf-ms{font-size:10.5px;color:#74879b;margin-top:3px}
      @media(max-width:900px){.hf-kpis{grid-template-columns:repeat(2,1fr)}.hf-foot{grid-template-columns:1fr}.hf-top{align-items:flex-start;flex-direction:column}}
    </style>
    """

    parts = [css, '<div class="hf-wrap">']
    parts.append(
        '<div class="hf-top"><div><div class="hf-title">Verificação do FCK por Corpo de Prova</div>'
        '<div class="hf-sub">Resultados de resistência à compressão • leitura consolidada por idade • alerta de consistência dos pares</div></div>'
        '<div class="hf-rule">FCK: <b>1 CP do par ≥ FCK = aprovado</b> &nbsp;•&nbsp; Pares: <b>Δ &gt; 2,0 MPa = revisar</b></div></div>'
    )
    parts.append(
        f'<div class="hf-kpis">'
        f'<div class="hf-kpi"><div class="hf-kl">FCK DE PROJETO</div><div class="hf-kv">{_html.escape(fck_txt)}</div><div class="hf-ks">Meta especificada</div></div>'
        f'<div class="hf-kpi"><div class="hf-kl">TOTAL DE CPs</div><div class="hf-kv">{total}</div><div class="hf-ks">Corpos de prova acompanhados</div></div>'
        f'<div class="hf-kpi"><div class="hf-kl">ATINGIRAM O FCK</div><div class="hf-kv" style="color:#65e887">{ok_count} <span style="font-size:14px;color:#9fb0c3">({ok_pct:.0f}%)</span></div><div class="hf-ks">Na última idade disponível</div></div>'
        f'<div class="hf-kpi"><div class="hf-kl">EM ACOMPANHAMENTO</div><div class="hf-kv" style="color:#ffd54a">{wait_count}</div><div class="hf-ks">Ainda sem situação final</div></div>'
        f'<div class="hf-kpi"><div class="hf-kl">ALERTAS DE PARES</div><div class="hf-kv" style="color:{pair_alert_color}">{pair_alert_count}</div><div class="hf-ks">Diferença interna &gt; 2,0 MPa</div></div>'
        f'</div>'
    )

    h1 = '<tr><th rowspan="2" style="text-align:left">CP</th>'
    for age in ages:
        h1 += f'<th colspan="2">{age} DIAS</th>'
    h1 += '<th rowspan="2">PARES</th><th rowspan="2">SITUAÇÃO FINAL</th></tr>'
    h2 = '<tr>' + ''.join('<th>MPa</th><th>Status</th>' for _ in ages) + '</tr>'

    rows = []
    for i, (_, r) in enumerate(dfp.iterrows()):
        row = [f'<tr><td class="hf-cp">{_html.escape(str(r.get("CP", "—")))}</td>']
        for age in ages:
            vals = []
            for c in _mp_cols(age):
                v = pd.to_numeric(pd.Series([r.get(c)]), errors="coerce").dropna()
                if not v.empty:
                    vals.append(_fmt(float(v.iloc[0])))
            values_txt = ' / '.join(vals) if vals else '—'
            sc = _status_col(age)
            status_raw = r.get(sc, '') if sc else ''
            kind = _status_kind(status_raw)
            label = _html.escape(_clean_status(status_raw))
            row.append(f'<td class="hf-val">{values_txt}</td>')
            row.append(f'<td><span class="hf-badge hf-{kind}"><span class="hf-dot"></span>{label}</span></td>')

        pair_alert = pair_alert_flags[i] if i < len(pair_alert_flags) else False
        if pair_alert:
            row.append('<td><span class="hf-badge hf-final hf-final-bad">REVISAR Δ&gt;2</span></td>')
        else:
            row.append('<td><span class="hf-badge hf-final hf-final-ok">OK</span></td>')

        state = final_states[i] if i < len(final_states) else 'wait'
        if state == 'ok':
            flabel, fclass = 'CONFORME', 'hf-final-ok'
        elif state == 'bad':
            flabel, fclass = 'NÃO CONFORME', 'hf-final-bad'
        else:
            flabel, fclass = 'EM ANÁLISE', 'hf-final-wait'
        row.append(f'<td><span class="hf-badge hf-final {fclass}">{flabel}</span></td></tr>')
        rows.append(''.join(row))

    parts.append('<div class="hf-box"><table class="hf-table"><thead>' + h1 + h2 + '</thead><tbody>' + ''.join(rows) + '</tbody></table></div>')
    parts.append(
        f'<div class="hf-foot">'
        f'<div class="hf-mini"><div class="hf-ml">MAIOR RESISTÊNCIA FINAL</div><div class="hf-mv">{max_final_txt}</div><div class="hf-ms">CP {_html.escape(max_cp)}</div></div>'
        f'<div class="hf-mini"><div class="hf-ml">MÉDIA DOS MELHORES RESULTADOS</div><div class="hf-mv">{media_final_txt}</div><div class="hf-ms">Referência: {age_final_txt}</div></div>'
        f'<div class="hf-mini"><div class="hf-ml">LEGENDA</div><div class="hf-mv" style="font-size:13px">🟢 Atingiu &nbsp; 🟡 Coletando &nbsp; 🔴 Não atingiu</div><div class="hf-ms">Valores em MPa</div></div>'
        f'</div>'
    )
    parts.append('</div>')
    st.markdown(''.join(parts), unsafe_allow_html=True)

def render_print_block(pdf_all: bytes, pdf_cp: Optional[bytes], brand: str, brand600: str):
    b64_all = base64.b64encode(pdf_all).decode()
    cp_btn = ""
    if pdf_cp:
        b64_cp = base64.b64encode(pdf_cp).decode()
        cp_btn = f'<button class="h-print-btn" onclick="habiPrint(\'{b64_cp}\')">🖨️ Imprimir — CP focado</button>'
    html = f"""
    <style>
      :root {{ --brand:{brand}; --brand-600:{brand600}; }}
      .printbar {{ display:flex; flex-wrap:wrap; gap:12px; margin:10px 0 6px 0; }}
      .h-print-btn {{
        background: linear-gradient(180deg, var(--brand), var(--brand-600));
        color:#fff; border:0; border-radius:999px; padding:10px 16px; font-weight:700; cursor:pointer;
        box-shadow:0 10px 20px rgba(0,0,0,.10);
      }}
    </style>
    <div class="printbar">
      <button class="h-print-btn" onclick="habiPrint('{b64_all}')">🖨️ Imprimir — Tudo</button>
      {cp_btn}
      <span style="font-size:12px;color:#6b7280">Permita pop-ups para imprimir</span>
    </div>
    <script>
      function habiPrint(b64) {{
        try {{
          var bin=atob(b64), len=bin.length, bytes=new Uint8Array(len);
          for (var i=0;i<len;i++) bytes[i]=bin.charCodeAt(i);
          var blob=new Blob([bytes], {{type:'application/pdf'}});
          var url=URL.createObjectURL(blob);
          var w=window.open('', '_blank');
          if(!w){{ alert('Habilite pop-ups para imprimir.'); return; }}
          w.document.write('<!doctype html><html><head><title>Imprimir</title>'+
            '<style>html,body{{margin:0;height:100%}}</style></head><body>'+
            '<iframe id="__pf" style="width:100%;height:100%;border:0"></iframe>'+
            '<script>var f=document.getElementById("__pf");f.onload=function(){{try{{f.contentWindow.focus();f.contentWindow.print();}}catch(e){{}}}};f.src="'+url+'#zoom=page-width";<\/script>'+
            '</body></html>');
          w.document.close();
        }} catch(e) {{ alert('Falha ao preparar impressão: '+e); }}
      }}
    </script>
    """
    st.components.v1.html(html, height=74)

# =============================================================================
# Uploader
# =============================================================================
st.markdown("<div id='upload-area'></div>", unsafe_allow_html=True)
st.markdown(_ui_section("Importar certificados", "Envie os PDFs de rompimento para iniciar a análise.", "📄", "PDF"), unsafe_allow_html=True)

BATCH_MODE = bool(s.get("BATCH_MODE", False))
_uploader_key = f"uploader_{'multi' if BATCH_MODE else 'single'}_{s['uploader_key']}"

if BATCH_MODE:
    uploaded_files = st.file_uploader("Arraste ou selecione os certificados", type=["pdf"], accept_multiple_files=True,
                                      key=_uploader_key, help="Carregue um ou mais PDFs de certificados.")
else:
    up1 = st.file_uploader("Arraste ou selecione um certificado", type=["pdf"], accept_multiple_files=False,
                           key=_uploader_key, help="Carregue um PDF de certificado.")
    uploaded_files = [up1] if up1 is not None else []

# =============================================================================
# Helpers de nome de arquivo
# =============================================================================
def _slugify_for_filename(text: str) -> str:
    import unicodedata, re as _re
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    t = _re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    return t or "relatorio"

def _safe_mode(series: pd.Series):
    if series is None or series.dropna().empty:
        return None
    try:
        m = series.mode()
        return None if m.empty else m.iat[0]
    except Exception:
        return series.dropna().iloc[0]

def _to_date_obj(d: str):
    from datetime import datetime as _dt
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return _dt.strptime(str(d), fmt).date()
        except Exception:
            pass
    return None

def _dd_mm_aaaa(d) -> str:
    try:
        return f"{int(d.day):02d}_{int(d.month):02d}_{int(d.year):04d}"
    except Exception:
        return ""

def _extract_rel_tail_from_files(uploaded_files: list) -> str | None:
    import re as _re
    for f in uploaded_files or []:
        fname = (getattr(f, "name", "") or "").lower()
        m = _re.search(r"(\d{3,6})[_\-]([0-9]{1,2}d)[_\-](\d{2}[_\-]\d{2}[_\-]\d{4})", fname)
        if m:
            rid = int(m.group(1)) % 1000
            return f"{rid:03d}_{m.group(2)}_{m.group(3).replace('-', '_')}"
        m2 = _re.search(r"(\d{3,6})", fname)
        if m2:
            rid = int(m2.group(1)) % 1000
            return f"{rid:03d}"
    return None

def _extract_rel_tail_from_df(df_view: pd.DataFrame) -> str | None:
    import re as _re
    if "Relatório" not in df_view.columns or df_view["Relatório"].dropna().empty:
        return None
    rel_mode = str(_safe_mode(df_view["Relatório"]))
    m = _re.search(r"(\d{3,})", rel_mode)
    if m:
        rid = int(m.group(1)) % 1000
        return f"{rid:03d}"
    return None

def _extract_age_token(df_view: pd.DataFrame) -> str | None:
    if "Idade (dias)" not in df_view.columns or df_view["Idade (dias)"].dropna().empty:
        return None
    ages = pd.to_numeric(df_view["Idade (dias)"], errors="coerce").dropna().astype(int)
    if ages.empty: return None
    age = _safe_mode(ages)
    return f"{int(age)}d" if age is not None else None

def _extract_cert_date_token(df_view: pd.DataFrame) -> str | None:
    if "Data Certificado" not in df_view.columns:
        return None
    dates = [_to_date_obj(x) for x in df_view["Data Certificado"].dropna().unique().tolist()]
    dates = [d for d in dates if d is not None]
    if not dates: return None
    return _dd_mm_aaaa(min(dates))

def build_pdf_filename(df_view: pd.DataFrame, uploaded_files: list) -> str:
    if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty:
        obra = _safe_mode(df_view["Obra"].astype(str)) or "Obra"
    else:
        obra = "Obra"
    obra_slug = _slugify_for_filename(obra)

    rel_tail = _extract_rel_tail_from_files(uploaded_files)
    age_tok  = _extract_age_token(df_view) or ""
    date_tok = _extract_cert_date_token(df_view) or ""

    if rel_tail and "_" in rel_tail and rel_tail.count("_") >= 2:
        final_tail = rel_tail
    else:
        rrr = rel_tail if (rel_tail and rel_tail.isdigit() and len(rel_tail) == 3) else (_extract_rel_tail_from_df(df_view) or "")
        tail_parts = [p for p in [rrr, age_tok, date_tok] if p]
        final_tail = "_".join(tail_parts)

    base = f"Relatorio_analise_certificado_obra_{obra_slug}"
    if final_tail:
        return f"{base}_{final_tail}.pdf"
    if date_tok:
        return f"{base}_{date_tok}.pdf"
    from datetime import datetime as _dt
    return f"{base}_{_dt.utcnow().strftime('%d_%m_%Y')}.pdf"

# =============================================================================
# VISÃO GERAL
# =============================================================================
def render_overview_and_tables(df_view: pd.DataFrame, stats_cp_idade: pd.DataFrame, TOL_MP: float, outliers_df: Optional[pd.DataFrame] = None):
    import pandas as _pd
    from datetime import datetime as _dt


    def _format_float_label_local(value: Optional[float]) -> str:
        if value is None or _pd.isna(value): return "—"
        num = float(value); label = f"{num:.2f}".rstrip("0").rstrip(".")
        return label or f"{num:.2f}"

    def _to_date(d):
        try: return _dt.strptime(str(d), "%d/%m/%Y").date()
        except Exception: return None

    obra_label = "—"; data_label = "—"; fck_label = "—"

    if not df_view.empty:
        ob = sorted(set(df_view["Obra"].astype(str)))
        obra_label = ob[0] if len(ob) == 1 else f"Múltiplas ({len(ob)})"
        fck_candidates: List[str] = []
        for raw in df_view["Fck Projeto"].tolist():
            normalized = _to_float_or_none(raw)
            if normalized is not None:
                formatted = _format_float_label_local(normalized)
                if formatted != "—": fck_candidates.append(formatted)
            else:
                raw_str = str(raw).strip()
                if raw_str and raw_str.lower() != "nan": fck_candidates.append(raw_str)
        if fck_candidates: fck_label = ", ".join(dict.fromkeys(fck_candidates))
        datas_validas = [_to_date(x) for x in df_view["Data Certificado"].unique()]
        datas_validas = [d for d in datas_validas if d is not None]
        if datas_validas:
            di, df_ = min(datas_validas), max(datas_validas)
            data_label = di.strftime('%d/%m/%Y') if di == df_ else f"{di.strftime('%d/%m/%Y')} — {df_.strftime('%d/%m/%Y')}"

    def _fmt_pct(v): return "--" if v is None else f"{v:.0f}%"

    fck_series_all = _pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
    fck_val = float(fck_series_all.mode().iloc[0]) if not fck_series_all.empty else None
    KPIs = compute_exec_kpis(df_view, fck_val)

    n_relatorios = df_view["Relatório"].nunique()
    media_txt = "--" if KPIs["media"] is None else f"{KPIs['media']:.1f} MPa"
    dp_txt = "--" if KPIs["dp"] is None else f"{KPIs['dp']:.1f}"
    snf = _pd.to_numeric(df_view.get("Abatimento NF (mm)"), errors="coerce")
    stol = _pd.to_numeric(df_view.get("Abatimento NF tol (mm)"), errors="coerce") if "Abatimento NF tol (mm)" in df_view.columns else _pd.Series(dtype=float)
    abat_nf_label = "—"
    if snf is not None and not snf.dropna().empty:
        v = float(snf.dropna().mode().iloc[0])
        if stol is not None and not stol.dropna().empty:
            t = float(stol.dropna().mode().iloc[0])
            abat_nf_label = f"{v:.0f} ± {t:.0f} mm"
        else:
            abat_nf_label = f"{v:.0f} mm"

    st.markdown(
        f"""
        <div class="ov-kpis">
          <div class="ov-card"><div class="ov-label">Obra</div><div class="ov-value">{obra_label}</div><div class="ov-hint">Empreendimento analisado</div></div>
          <div class="ov-card"><div class="ov-label">Certificados</div><div class="ov-value">{data_label}</div><div class="ov-hint">Período selecionado</div></div>
          <div class="ov-card"><div class="ov-label">FCK de projeto</div><div class="ov-value">{fck_label} MPa</div><div class="ov-hint">Resistência especificada</div></div>
          <div class="ov-card"><div class="ov-label">CPs ≥ FCK · 28d</div><div class="ov-value">{_fmt_pct(KPIs['pct28'])}</div><div class="ov-hint">Melhor resultado do par</div></div>
          <div class="ov-card"><div class="ov-label">CPs ≥ FCK · 63d</div><div class="ov-value">{_fmt_pct(KPIs['pct63'])}</div><div class="ov-hint">Melhor resultado do par</div></div>
          <div class="ov-card"><div class="ov-label">Relatórios</div><div class="ov-value">{n_relatorios}</div><div class="ov-hint">Documentos no filtro atual</div></div>
        </div>
        <div class="ov-kpis" style="grid-template-columns:repeat(4,minmax(0,1fr));">
          <div class="ov-card"><div class="ov-label">Média geral</div><div class="ov-value">{media_txt}</div><div class="ov-hint">Todas as leituras visíveis</div></div>
          <div class="ov-card"><div class="ov-label">Desvio-padrão</div><div class="ov-value">{dp_txt}</div><div class="ov-hint">Dispersão global</div></div>
          <div class="ov-card"><div class="ov-label">Abatimento NF</div><div class="ov-value">{abat_nf_label}</div><div class="ov-hint">Valor predominante</div></div>
          <div class="ov-card"><div class="ov-label">Tolerância Real × Est.</div><div class="ov-value">±{TOL_MP:.1f} MPa</div><div class="ov-hint">Faixa de comparação</div></div>
        </div>
        """,
        unsafe_allow_html=True
    )

    material_label, norma_label, dimensao_label = _resumo_material_norma_df(df_view)
    st.markdown(
        f"""
        <div class="ov-tech">
          <div class="ov-tech-card"><div class="ov-tech-label">Material</div><div class="ov-tech-value">{material_label}</div></div>
          <div class="ov-tech-card"><div class="ov-tech-label">Norma técnica</div><div class="ov-tech-value">{norma_label}</div></div>
          <div class="ov-tech-card"><div class="ov-tech-label">Corpo de prova</div><div class="ov-tech-value">{dimensao_label}</div></div>
        </div>
        <div class="ov-semaforo">
          <div>
            <div class="ov-semaforo-main" style="color:{KPIs['status_cor']}">{KPIs['status_txt']}</div>
            <div class="ov-semaforo-sub">Semáforo executivo: 28 dias = 60% · 63 dias = 40%</div>
          </div>
          <div class="ov-semaforo-sub">≥ 90% Bom &nbsp;•&nbsp; ≥ 75% Atenção &nbsp;•&nbsp; &lt; 75% Crítico</div>
        </div>
        """,
        unsafe_allow_html=True
    )

    t_res, t_stats, t_alerts = st.tabs(["📋 Resultados individuais", "📐 Estatísticas por CP", "⚠️ Alertas / outliers"])
    with t_res:
        render_screen_table(df_view, "Resultados individuais", "Leituras extraídas dos certificados selecionados.")
    with t_stats:
        render_screen_table(stats_cp_idade, "Estatísticas por CP e idade", "Média, desvio-padrão e quantidade de leituras.")
    with t_alerts:
        if outliers_df is not None and not outliers_df.empty:
            render_screen_table(outliers_df, "CPs fora da curva", "Resultados acima do limite de sigma configurado.")
        else:
            st.success("Nenhum outlier identificado para o limite de sigma atual.")

# =============================================================================
# CENTRAL DE ALERTAS TÉCNICOS / MAPA DE CALOR
# =============================================================================
def build_technical_alert_snapshot(
    df_: pd.DataFrame,
    tol_mp: float,
    outliers_df: Optional[pd.DataFrame] = None,
    viol_cp: Optional[list] = None,
    viol_nf: Optional[list] = None,
) -> Dict[str, Any]:
    """Consolida ocorrências técnicas para leitura rápida, sem alterar regras de aprovação."""
    import pandas as _pd

    ages = [1, 3, 7, 14, 21, 28, 56, 63]
    final_ages = [63, 56, 28]
    empty = {
        "problem_cps": set(), "pair_cps": set(), "fck_bad_cps": set(),
        "real_est_cps": set(), "outlier_cps": set(), "duplicate_cps": set(),
        "nf_duplicate_cps": set(), "pair_details": [], "fck_details": [],
        "real_est_details": [], "attention_df": _pd.DataFrame(), "heatmap": {},
        "ages": [], "fck": None, "est_map": {}, "pending_final": 0,
    }
    if df_ is None or df_.empty or "CP" not in df_.columns:
        return empty

    d = df_.copy()
    d["CP"] = d["CP"].astype(str)
    d["Idade (dias)"] = _pd.to_numeric(d["Idade (dias)"], errors="coerce")
    d["Resistência (MPa)"] = _pd.to_numeric(d["Resistência (MPa)"], errors="coerce")
    d = d.dropna(subset=["Idade (dias)", "Resistência (MPa)"])
    if d.empty:
        return empty
    d["Idade (dias)"] = d["Idade (dias)"].astype(int)

    fck_s = _pd.to_numeric(d.get("Fck Projeto"), errors="coerce").dropna()
    fck_val = float(fck_s.mode().iloc[0]) if not fck_s.empty else None

    # Curva de referência: mantém exatamente a mesma lógica dos gráficos atuais.
    est_map = {}
    m28 = d.loc[d["Idade (dias)"] == 28, "Resistência (MPa)"].mean()
    m7 = d.loc[d["Idade (dias)"] == 7, "Resistência (MPa)"].mean()
    if _pd.notna(m28):
        est_map = {7: float(m28) * 0.65, 28: float(m28), 63: float(m28) * 1.15}
    elif _pd.notna(m7):
        f28e = float(m7) / 0.70
        est_map = {7: float(m7), 28: f28e, 63: f28e * 1.15}

    pair_details = []
    pair_cps = set()
    pair_delta_by_cp_age = {}
    for (cp, age), g in d.groupby(["CP", "Idade (dias)"], sort=False):
        vals = g["Resistência (MPa)"].dropna().astype(float)
        if len(vals) >= 2:
            vmin, vmax = float(vals.min()), float(vals.max())
            delta = vmax - vmin
            pair_delta_by_cp_age[(str(cp), int(age))] = delta
            if delta > 2.0:
                pair_cps.add(str(cp))
                pair_details.append({
                    "CP": str(cp), "Idade (dias)": int(age),
                    "Menor (MPa)": vmin, "Maior (MPa)": vmax,
                    "Δ par (MPa)": delta,
                })

    # FCK: mesma leitura do painel — usa a idade final mais avançada disponível
    # e, dentro do par, considera o melhor resultado.
    fck_bad_cps = set()
    fck_details = []
    pending_final = 0
    if fck_val is not None:
        for cp, g in d.groupby("CP", sort=False):
            chosen_age = None
            vals = None
            for age in final_ages:
                vv = g.loc[g["Idade (dias)"] == age, "Resistência (MPa)"].dropna().astype(float)
                if not vv.empty:
                    chosen_age, vals = age, vv
                    break
            if chosen_age is None:
                pending_final += 1
                continue
            best = float(vals.max())
            if best < float(fck_val):
                cp_s = str(cp)
                fck_bad_cps.add(cp_s)
                fck_details.append({
                    "CP": cp_s, "Idade final (dias)": int(chosen_age),
                    "Melhor resultado (MPa)": best, "FCK (MPa)": float(fck_val),
                    "Déficit (MPa)": float(fck_val) - best,
                })

    # Real x estimado: ponto a ponto nas idades para as quais existe referência.
    real_est_cps = set()
    real_est_details = []
    for _, r in d.iterrows():
        age = int(r["Idade (dias)"])
        if age not in est_map:
            continue
        real = float(r["Resistência (MPa)"])
        est = float(est_map[age])
        delta = real - est
        if abs(delta) > float(tol_mp):
            cp_s = str(r["CP"])
            real_est_cps.add(cp_s)
            real_est_details.append({
                "CP": cp_s, "Idade (dias)": age, "Real (MPa)": real,
                "Estimado (MPa)": est, "Δ Real-Est. (MPa)": delta,
            })

    outlier_cps = set()
    if outliers_df is not None and not outliers_df.empty and "CP" in outliers_df.columns:
        outlier_cps = set(outliers_df["CP"].dropna().astype(str).tolist())

    present_cps = set(d["CP"].dropna().astype(str).tolist())
    duplicate_cps = set(map(str, viol_cp or [])) & present_cps
    nf_duplicate_cps = set()
    if viol_nf and "Nota Fiscal" in d.columns:
        nf_duplicate_cps = set(
            d.loc[d["Nota Fiscal"].astype(str).isin(set(map(str, viol_nf))), "CP"]
             .dropna().astype(str).tolist()
        )

    # Índice de atenção = quantidade de categorias distintas com ocorrência.
    reasons = {cp: [] for cp in present_cps}
    for cp in pair_cps: reasons.setdefault(cp, []).append("Δ par > 2 MPa")
    for cp in fck_bad_cps: reasons.setdefault(cp, []).append("FCK não atingido")
    for cp in real_est_cps: reasons.setdefault(cp, []).append("Real × estimado fora da tolerância")
    for cp in outlier_cps: reasons.setdefault(cp, []).append("Outlier")
    for cp in duplicate_cps: reasons.setdefault(cp, []).append("CP em relatórios diferentes")
    for cp in nf_duplicate_cps: reasons.setdefault(cp, []).append("NF em relatórios diferentes")

    attention_rows = []
    for cp, rs in reasons.items():
        if rs:
            attention_rows.append({
                "CP": cp,
                "Índice de atenção": len(rs),
                "Ocorrências": " • ".join(rs),
            })
    attention_df = _pd.DataFrame(attention_rows)
    if not attention_df.empty:
        attention_df = attention_df.sort_values(
            ["Índice de atenção", "CP"], ascending=[False, True], kind="stable"
        ).reset_index(drop=True)

    problem_cps = pair_cps | fck_bad_cps | real_est_cps | outlier_cps | duplicate_cps | nf_duplicate_cps

    # Mapa de calor: cada célula representa a leitura consolidada CP × idade.
    heatmap = {}
    ages_present = [a for a in ages if a in set(d["Idade (dias)"].tolist())]
    for cp, g in d.groupby("CP", sort=False):
        cp_s = str(cp)
        heatmap[cp_s] = {}
        for age in ages_present:
            vals = g.loc[g["Idade (dias)"] == age, "Resistência (MPa)"].dropna().astype(float)
            if vals.empty:
                heatmap[cp_s][age] = {"state": "none", "text": "—", "title": "Sem dados"}
                continue

            best, meanv = float(vals.max()), float(vals.mean())
            pair_delta = pair_delta_by_cp_age.get((cp_s, age))
            pair_bad = pair_delta is not None and pair_delta > 2.0
            fck_bad = age in (28, 56, 63) and fck_val is not None and best < float(fck_val)
            fck_ok = age in (28, 56, 63) and fck_val is not None and best >= float(fck_val)
            curve_bad = age in est_map and any(abs(float(v) - float(est_map[age])) > float(tol_mp) for v in vals)

            title = f"Resultados: {' / '.join(f'{float(v):.2f}' for v in vals)} MPa"
            if pair_delta is not None:
                title += f" | Δ par: {pair_delta:.2f} MPa"

            if fck_bad and pair_bad:
                state, text = "badpair", f"🔴 FCK · 🟠 Δ{pair_delta:.1f}"
            elif fck_bad:
                state, text = "bad", f"🔴 {best:.1f}"
            elif pair_bad:
                state, text = "pair", f"🟠 Δ{pair_delta:.1f}"
            elif fck_ok:
                state, text = "ok", f"🟢 {best:.1f}"
            elif curve_bad:
                state, text = "curve", f"🟣 {meanv:.1f}"
            else:
                state, text = "wait", f"🟡 {meanv:.1f}"
            heatmap[cp_s][age] = {"state": state, "text": text, "title": title}

    return {
        "problem_cps": problem_cps,
        "pair_cps": pair_cps,
        "fck_bad_cps": fck_bad_cps,
        "real_est_cps": real_est_cps,
        "outlier_cps": outlier_cps,
        "duplicate_cps": duplicate_cps,
        "nf_duplicate_cps": nf_duplicate_cps,
        "pair_details": pair_details,
        "fck_details": fck_details,
        "real_est_details": real_est_details,
        "attention_df": attention_df,
        "heatmap": heatmap,
        "ages": ages_present,
        "fck": fck_val,
        "est_map": est_map,
        "pending_final": pending_final,
    }


def render_technical_alert_center(snapshot: Dict[str, Any], has_nf_violation: bool = False,
                                  has_cp_violation: bool = False, multiple_fck_detected: bool = False):
    """Central visual para decidir rapidamente onde concentrar a revisão."""
    import html as _html

    problem_cps = snapshot.get("problem_cps", set())
    pair_cps = snapshot.get("pair_cps", set())
    fck_bad_cps = snapshot.get("fck_bad_cps", set())
    real_est_cps = snapshot.get("real_est_cps", set())
    outlier_cps = snapshot.get("outlier_cps", set())
    dup_cps = snapshot.get("duplicate_cps", set()) | snapshot.get("nf_duplicate_cps", set())
    pair_occ = len(snapshot.get("pair_details", []))
    real_occ = len(snapshot.get("real_est_details", []))

    if problem_cps:
        banner_title = "OCORRÊNCIAS TÉCNICAS DETECTADAS"
        banner_sub = f"{len(problem_cps)} CP(s) merecem conferência prioritária. Clique nos cartões para ir direto à análise relacionada."
        banner_color = "#ff5e7a"
        banner_bg = "rgba(255,94,122,.10)"
    else:
        banner_title = "SEM OCORRÊNCIAS TÉCNICAS CRÍTICAS NO FILTRO ATUAL"
        banner_sub = "O painel não encontrou FCK final não atendido, pares com Δ > 2 MPa, desvios Real × Estimado ou outliers."
        banner_color = "#00e676"
        banner_bg = "rgba(0,230,118,.08)"

    integrity_n = len(dup_cps)
    if has_nf_violation and not snapshot.get("nf_duplicate_cps"):
        integrity_n += 1
    if has_cp_violation and not snapshot.get("duplicate_cps"):
        integrity_n += 1

    cards = [
        ("FCK NÃO ATINGIDO", len(fck_bad_cps), "CPs na idade final disponível", "#ff3b5c", "#fck-verification"),
        ("PARES Δ > 2 MPa", len(pair_cps), f"{pair_occ} ocorrência(s)", "#ff8a00", "#pair-analysis"),
        ("REAL × ESTIMADO", len(real_est_cps), f"{real_occ} ponto(s) fora da tolerância", "#8b5cf6", "#graphs"),
        ("OUTLIERS", len(outlier_cps), "Limite sigma configurado", "#00e5ff", "#overview-alerts"),
        ("INTEGRIDADE", integrity_n, "CP/NF em relatórios diferentes", "#ffd60a", "#overview-alerts"),
        ("CPs PRIORITÁRIOS", len(problem_cps), "União das ocorrências", "#ff5e7a" if problem_cps else "#00e676", "#attention-map"),
    ]

    html = [f"""
    <style>
      .ta-wrap{{background:linear-gradient(145deg,#050b12,#07121e 58%,#081724);border:1px solid #19364a;border-radius:20px;padding:16px;margin:8px 0 14px;box-shadow:0 18px 46px rgba(0,0,0,.22)}}
      .ta-banner{{border:1px solid {banner_color}55;background:{banner_bg};border-radius:14px;padding:12px 14px;margin-bottom:12px;box-shadow:inset 4px 0 0 {banner_color}}}
      .ta-title{{font-size:17px;font-weight:950;color:{banner_color};letter-spacing:.25px}}.ta-sub{{font-size:11.5px;color:#9fb0c3;margin-top:4px}}
      .ta-grid{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:9px}}
      .ta-card{{display:block;text-decoration:none;background:linear-gradient(180deg,#0d1c2b,#091522);border:1px solid #203b50;border-radius:13px;padding:11px 12px;min-height:86px;transition:.15s ease}}
      .ta-card:hover{{transform:translateY(-2px);border-color:#3d617c;box-shadow:0 10px 24px rgba(0,0,0,.22)}}
      .ta-label{{font-size:9.5px;color:#88a0b6;font-weight:850;letter-spacing:.45px}}.ta-value{{font-size:24px;font-weight:950;margin-top:7px;line-height:1}}.ta-hint{{font-size:9.8px;color:#6f879d;margin-top:7px;line-height:1.25}}
      @media(max-width:1200px){{.ta-grid{{grid-template-columns:repeat(3,1fr)}}}} @media(max-width:720px){{.ta-grid{{grid-template-columns:repeat(2,1fr)}}}}
    </style>
    <div class="ta-wrap"><div class="ta-banner"><div class="ta-title">⚡ {_html.escape(banner_title)}</div><div class="ta-sub">{_html.escape(banner_sub)}</div></div><div class="ta-grid">
    """]
    for label, value, hint, color, href in cards:
        html.append(
            f'<a class="ta-card" href="{href}"><div class="ta-label">{_html.escape(label)}</div>'
            f'<div class="ta-value" style="color:{color};text-shadow:0 0 18px {color}33">{value}</div>'
            f'<div class="ta-hint">{_html.escape(hint)}</div></a>'
        )
    html.append('</div></div>')
    st.markdown(''.join(html), unsafe_allow_html=True)

    att = snapshot.get("attention_df")
    if att is not None and not att.empty:
        top = att.head(8).copy()
        st.markdown(
            "<div id='attention-map'></div><div class='ui-table-title'>🎯 CPs prioritários para revisão</div>"
            "<div class='ui-table-sub' style='margin-bottom:7px'>Ordenados pelo número de categorias de ocorrência, sem atribuir aprovação ou reprovação adicional.</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(top, use_container_width=True, hide_index=True, height=min(110 + 35 * len(top), 390))


def render_cp_heatmap(snapshot: Dict[str, Any], only_problem: bool = False):
    """Mapa de calor textual CP × idade com prioridade para ocorrências técnicas."""
    import html as _html
    heatmap = snapshot.get("heatmap", {}) or {}
    ages = snapshot.get("ages", []) or []
    problem_cps = set(snapshot.get("problem_cps", set()) or set())
    if not heatmap or not ages:
        return

    cps = list(heatmap.keys())
    if only_problem:
        cps = [cp for cp in cps if cp in problem_cps]
    if not cps:
        st.success("Modo 'somente problemas': nenhum CP com ocorrência técnica no filtro atual.")
        return

    def _cp_key(cp):
        import re as _re
        m = _re.search(r"(\d+)", str(cp))
        return (int(m.group(1)) if m else 10**12, str(cp))
    cps = sorted(cps, key=_cp_key)

    css = """
    <style>
      .hm-wrap{overflow-x:auto;border:1px solid #1b3a4f;border-radius:15px;background:#06111b;margin:7px 0 16px;box-shadow:0 12px 28px rgba(0,0,0,.17)}
      .hm-table{width:100%;min-width:850px;border-collapse:separate;border-spacing:4px;padding:5px;color:#e8f2fb;font-size:11px}
      .hm-table th{padding:8px 7px;color:#91a9bf;font-size:10px;letter-spacing:.35px}.hm-cp{text-align:left!important;font-weight:900;color:#f8fafc!important;white-space:nowrap;padding:0 10px!important}
      .hm-cell{border-radius:9px;padding:8px 7px!important;text-align:center;white-space:nowrap;border:1px solid transparent;font-weight:850}
      .hm-none{background:#0c1823;color:#64788b;border-color:#172b3b}.hm-wait{background:rgba(250,204,21,.10);color:#ffe067;border-color:rgba(250,204,21,.22)}
      .hm-ok{background:rgba(0,230,118,.11);color:#65f1a2;border-color:rgba(0,230,118,.25)}.hm-bad{background:rgba(255,59,92,.14);color:#ff7188;border-color:rgba(255,59,92,.30)}
      .hm-pair{background:rgba(255,138,0,.14);color:#ffad42;border-color:rgba(255,138,0,.32)}.hm-curve{background:rgba(139,92,246,.14);color:#b99cff;border-color:rgba(139,92,246,.30)}
      .hm-badpair{background:linear-gradient(135deg,rgba(255,59,92,.18),rgba(255,138,0,.15));color:#ffd0a1;border-color:rgba(255,94,122,.38)}
    </style>
    """
    parts = [css, '<div class="hm-wrap"><table class="hm-table"><thead><tr><th style="text-align:left">CP</th>']
    for age in ages:
        parts.append(f'<th>{age} DIAS</th>')
    parts.append('</tr></thead><tbody>')
    for cp in cps:
        parts.append(f'<tr><td class="hm-cp">{_html.escape(str(cp))}</td>')
        for age in ages:
            cell = heatmap.get(cp, {}).get(age, {"state":"none","text":"—","title":"Sem dados"})
            parts.append(
                f'<td class="hm-cell hm-{_html.escape(str(cell.get("state","none")))}" title="{_html.escape(str(cell.get("title","")))}">'
                f'{_html.escape(str(cell.get("text","—")))}</td>'
            )
        parts.append('</tr>')
    parts.append('</tbody></table></div>')
    st.markdown(''.join(parts), unsafe_allow_html=True)
    st.caption("Mapa: 🟢 FCK atendido na idade final • 🔴 FCK não atendido • 🟠 Δ do par > 2 MPa • 🟣 Real × Estimado fora da tolerância • 🟡 acompanhamento.")


# =============================================================================
# Pipeline principal
# =============================================================================
if uploaded_files:
    frames = []
    progress_holder = st.empty()
    for idx, f in enumerate(uploaded_files, start=1):
        if f is None: continue
        progress_holder.info(f"📥 Lendo PDF {idx}/{len(uploaded_files)}: {getattr(f,'name','arquivo.pdf')}")
        df_i, obra_i, data_i, fck_i = extrair_dados_certificado(f)
        if not df_i.empty:
            df_i["Data Certificado"] = data_i
            df_i["Obra"] = obra_i
            if "Fck Projeto" in df_i.columns:
                scalar_fck = _to_float_or_none(fck_i)
                if scalar_fck is not None:
                    df_i["Fck Projeto"] = pd.to_numeric(df_i["Fck Projeto"], errors="coerce").fillna(float(scalar_fck))
            else:
                df_i["Fck Projeto"] = fck_i
            df_i["Arquivo"] = getattr(f, "name", "arquivo.pdf")
            frames.append(df_i)
            log_event("file_parsed", {
                "file": getattr(f, "name", "arquivo.pdf"),
                "rows": int(df_i.shape[0]),
                "relatorios": int(df_i["Relatório"].nunique()),
                "obra": obra_i,
                "data_cert": data_i,
            })
    progress_holder.empty()

    if not frames:
        st.error("⚠️ Não encontrei CPs válidos nos PDFs enviados.")
    else:
        df = pd.concat(frames, ignore_index=True)
        # Atualiza material/norma/corpo de prova linha a linha antes das validações.
        # Isso evita que certificados mistos fiquem presos no primeiro material detectado.
        df = _atualizar_material_norma_linhas(df)

        # ===== Validações
        has_nf_violation = False
        has_cp_violation = False
        viol_nf = []
        viol_cp = []

        if not df.empty:
            nf_rel = df.dropna(subset=["Nota Fiscal","Relatório"]).astype({"Relatório": str})
            nf_multi = (nf_rel.groupby(["Nota Fiscal"])["Relatório"].nunique().reset_index(name="n_rel"))
            viol_nf = nf_multi[nf_multi["n_rel"] > 1]["Nota Fiscal"].tolist()
            if viol_nf:
                has_nf_violation = True
                detalhes = (nf_rel[nf_rel["Nota Fiscal"].isin(viol_nf)]
                            .groupby(["Nota Fiscal","Relatório"])["CP"].nunique().reset_index())
                st.error("🚨 **Nota Fiscal repetida em relatórios diferentes!** Confira o PDF de origem.")
                render_screen_table(detalhes.rename(columns={"CP":"#CPs distintos"}), "Notas fiscais repetidas", "A mesma NF foi localizada em relatórios diferentes.")
                try:
                    log_event("violation_nf_duplicate", {
                        "nf_list": list(map(str, viol_nf)),
                        "details": detalhes.to_dict(orient="records")
                    }, level="WARN")
                except Exception:
                    pass

            cp_rel = df.dropna(subset=["CP","Relatório"]).astype({"Relatório": str})
            cp_multi = (cp_rel.groupby(["CP"])["Relatório"].nunique().reset_index(name="n_rel"))
            viol_cp = cp_multi[cp_multi["n_rel"] > 1]["CP"].tolist()
            if viol_cp:
                has_cp_violation = True
                detalhes_cp = (cp_rel[cp_rel["CP"].isin(viol_cp)]
                               .groupby(["CP","Relatório"])["Idade (dias)"].count().reset_index(name="#leituras"))
                st.error("🚨 **CP repetido em relatórios diferentes!**")
                render_screen_table(detalhes_cp, "CPs repetidos", "O mesmo CP foi localizado em relatórios diferentes.")
                try:
                    log_event("violation_cp_duplicate", {
                        "cp_list": list(map(str, viol_cp)),
                        "details": detalhes_cp.to_dict(orient="records")
                    }, level="WARN")
                except Exception:
                    pass

        # ---------------- Filtros (corrigido p/ não quebrar)
        st.markdown(_ui_section("Filtros da análise", "Refine relatórios, período e FCK antes de visualizar os resultados.", "🔎", "FILTROS"), unsafe_allow_html=True)
        fc1, fc2, fc3 = st.columns([2.0, 2.0, 1.0])

        with fc1:
            rels = sorted(df["Relatório"].astype(str).unique())
            saved_rels = s.get("last_sel_rels") or []
            # garante que o default só tenha opções válidas
            default_rels = [str(r) for r in saved_rels if str(r) in rels]
            if not default_rels:
                default_rels = rels  # se nada bate, usa todos
            if rels:
                sel_rels = st.multiselect("Relatórios", rels, default=default_rels)
            else:
                sel_rels = st.multiselect("Relatórios", [], default=[])

        def to_date(d):
            try: return datetime.strptime(str(d), "%d/%m/%Y").date()
            except Exception: return None
        df["_DataObj"] = df["Data Certificado"].apply(to_date)
        valid_dates = [d for d in df["_DataObj"] if d is not None]

        with fc2:
            if valid_dates:
                dmin, dmax = min(valid_dates), max(valid_dates)
                last_range = s.get("last_date_range")
                if last_range:
                    ld_ini, ld_fim = last_range
                    if ld_ini < dmin or ld_ini > dmax or ld_fim < dmin or ld_fim > dmax:
                        last_range = (dmin, dmax)
                else:
                    last_range = (dmin, dmax)
                dini, dfim = st.date_input("Intervalo de data do certificado", last_range)
            else:
                dini, dfim = None, None

        with fc3:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("🔄 Limpar filtros / Novo upload", use_container_width=True):
                s["uploader_key"] += 1
                st.rerun()

        s["last_sel_rels"] = sel_rels
        if dini and dfim:
            s["last_date_range"] = (dini, dfim)

        mask = df["Relatório"].astype(str).isin(sel_rels) if sel_rels else df["Relatório"].astype(str).isin(rels)
        if valid_dates and dini and dfim:
            mask = mask & df["_DataObj"].apply(lambda d: d is not None and dini <= d <= dfim)
        df_view = df.loc[mask].drop(columns=["_DataObj"]).copy()

        # Gestão de múltiplos fck
        df_view["_FckLabel"] = df_view["Fck Projeto"].apply(_normalize_fck_label)
        fck_labels = list(dict.fromkeys(df_view["_FckLabel"]))
        multiple_fck_detected = len(fck_labels) > 1
        if multiple_fck_detected:
            st.warning("Detectamos múltiplos fck no conjunto selecionado. Escolha qual deseja analisar.")
            selected_fck_label = st.selectbox("fck para análise", fck_labels,
                                              format_func=lambda lbl: lbl if lbl != "—" else "Não informado")
            df_view = df_view[df_view["_FckLabel"] == selected_fck_label].copy()
        else:
            selected_fck_label = fck_labels[0] if fck_labels else "—"
        df_view = df_view.drop(columns=["_FckLabel"], errors="ignore")

        # Recalcula material/norma/corpo de prova conforme o fck selecionado.
        # Ex.: fck 6 = Argamassa, fck 25 = Concreto, fck 15 = Graute quando o bloco indicar graute.
        df_view = _atualizar_material_norma_linhas(df_view)

        if df_view.empty:
            st.info("Nenhum dado disponível para o fck selecionado.")
            st.stop()

        # ===== Estatísticas por CP/Idade
        stats_cp_idade = (
            df_view.groupby(["CP", "Idade (dias)"])["Resistência (MPa)"]
                  .agg(Média="mean", Desvio_Padrão="std", n="count").reset_index()
        )

        # ===== Outliers (simples com sigma do state)
        outliers_df = None
        try:
            df_num = df_view[["CP","Idade (dias)","Resistência (MPa)"]].copy()
            df_num["Resistência (MPa)"] = pd.to_numeric(df_num["Resistência (MPa)"], errors="coerce")
            sigma = float(s.get("OUTLIER_SIGMA", 3.0))
            outs = []
            for age, sub in df_num.groupby("Idade (dias)"):
                m = sub["Resistência (MPa)"].mean()
                sd = sub["Resistência (MPa)"].std()
                if pd.isna(sd) or sd == 0:
                    continue
                z = (sub["Resistência (MPa)"] - m) / sd
                mask_out = z.abs() > sigma
                if mask_out.any():
                    tmp = sub[mask_out].copy()
                    tmp["z"] = z[mask_out]
                    outs.append(tmp)
            if outs:
                outliers_df = pd.concat(outs).sort_values(["Idade (dias)","CP"])
        except Exception:
            outliers_df = None

        # ---------------------------------------------------------------
        # CENTRAL DE ALERTAS TÉCNICOS — leitura imediata antes das análises
        # ---------------------------------------------------------------
        tech_snapshot = build_technical_alert_snapshot(
            df_view,
            float(s["TOL_MP"]),
            outliers_df=outliers_df,
            viol_cp=viol_cp,
            viol_nf=viol_nf,
        )
        st.markdown("<div id='technical-alerts'></div>", unsafe_allow_html=True)
        st.markdown(_ui_section(
            "Central de Alertas Técnicos",
            "Identifique primeiro os CPs que merecem revisão e depois aprofunde a análise.",
            "⚡", "DIAGNÓSTICO"
        ), unsafe_allow_html=True)
        render_technical_alert_center(
            tech_snapshot,
            has_nf_violation=has_nf_violation,
            has_cp_violation=has_cp_violation,
            multiple_fck_detected=multiple_fck_detected,
        )

        problem_cps = set(tech_snapshot.get("problem_cps", set()) or set())
        only_problem_mode = st.toggle(
            "🔍 Mostrar somente CPs com ocorrência técnica",
            value=False,
            key="only_problem_cps",
            disabled=(len(problem_cps) == 0),
            help="Filtra as análises abaixo para CPs com FCK não atingido, Δ do par > 2 MPa, Real × Estimado fora da tolerância, outlier ou duplicidade."
        )

        if only_problem_mode and problem_cps:
            total_before = int(df_view["CP"].astype(str).nunique())
            df_view = df_view[df_view["CP"].astype(str).isin(problem_cps)].copy()
            total_after = int(df_view["CP"].astype(str).nunique())
            st.markdown(
                f"<div style='margin:7px 0 10px;padding:10px 12px;border-radius:11px;border:1px solid rgba(255,94,122,.30);background:rgba(255,94,122,.07);font-size:12px;color:var(--muted)'>"
                f"🔍 <b style='color:#ff5e7a'>Modo revisão ativo:</b> mostrando {total_after} de {total_before} CPs. "
                f"As análises e exportações abaixo seguem este filtro enquanto ele estiver ativo.</div>",
                unsafe_allow_html=True,
            )
            stats_cp_idade = (
                df_view.groupby(["CP", "Idade (dias)"])["Resistência (MPa)"]
                       .agg(Média="mean", Desvio_Padrão="std", n="count").reset_index()
            )
            if outliers_df is not None and not outliers_df.empty:
                outliers_df = outliers_df[outliers_df["CP"].astype(str).isin(problem_cps)].copy()

        st.markdown(
            "<div id='attention-map'></div>" +
            _ui_section(
                "Mapa de calor dos CPs",
                "Leitura rápida por idade: cores e símbolos mostram onde concentrar a conferência.",
                "▦", "MAPA TÉCNICO"
            ),
            unsafe_allow_html=True,
        )
        render_cp_heatmap(tech_snapshot, only_problem=bool(only_problem_mode))

        # ---------------------------------------------------------------
        # NAVEGAÇÃO PRINCIPAL DA ANÁLISE
        # ---------------------------------------------------------------

        # ---------------------------------------------------------------
        # SEÇÃO 1 — dados lidos / visão geral
        # ---------------------------------------------------------------
        st.markdown("<div id='overview-alerts'></div>", unsafe_allow_html=True)
        st.markdown(_ui_section("Visão geral", "Indicadores, resultados individuais, estatísticas e alertas do conjunto selecionado.", "▦", "RESUMO"), unsafe_allow_html=True)
        st.markdown("<div style='font-size:12px;color:var(--muted);margin:2px 0 10px'>Dados estruturados com sucesso a partir dos certificados selecionados.</div>", unsafe_allow_html=True)
        render_overview_and_tables(df_view, stats_cp_idade, float(s["TOL_MP"]), outliers_df=outliers_df)

        # ---------------------------------------------------------------
        # SEÇÃO 2 — gráficos
        # ---------------------------------------------------------------
        st.markdown("<div id='graphs'></div>", unsafe_allow_html=True)
        st.markdown(_ui_section("Análises gráficas", "Visualize a evolução da resistência e compare resultados reais e estimados.", "📈", "4 GRÁFICOS"), unsafe_allow_html=True)
        gc1, gc2 = st.columns([1.2, 1.2])
        with gc1:
            cp_foco_manual = st.text_input("Focar em um CP (opcional)", "", key="cp_manual", placeholder="Ex.: 048.214")
        with gc2:
            cp_select = st.selectbox("Selecionar CP", ["(Todos)"] + sorted(df_view["CP"].astype(str).unique()), key="cp_select")
        cp_focus = (cp_foco_manual.strip() or (cp_select if cp_select != "(Todos)" else "")).strip()
        if cp_focus:
            st.markdown(f"<div class='ui-chip' style='display:inline-flex;margin:0 0 10px'>Foco atual: CP {cp_focus}</div>", unsafe_allow_html=True)
        df_plot = df_view[df_view["CP"].astype(str) == cp_focus].copy() if cp_focus else df_view.copy()

        fck_series_focus = pd.to_numeric(df_plot["Fck Projeto"], errors="coerce").dropna()
        fck_series_all_g = pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
        fck_active = float(fck_series_focus.mode().iloc[0]) if not fck_series_focus.empty else (
            float(fck_series_all_g.mode().iloc[0]) if not fck_series_all_g.empty else None
        )

        stats_all_focus = df_plot.groupby("Idade (dias)")["Resistência (MPa)"].agg(mean="mean", std="std", count="count").reset_index()

        # === Gráfico 1
        fig1, ax = plt.subplots(figsize=(9.6, 4.9))
        for cp, sub in df_plot.groupby("CP"):
            sub = sub.sort_values("Idade (dias)")
            ax.plot(sub["Idade (dias)"], sub["Resistência (MPa)"], marker="o", linewidth=1.6, label=f"CP {cp}")
        sa_dp = stats_all_focus[stats_all_focus["count"] >= 2].copy()
        if not sa_dp.empty:
            ax.plot(sa_dp["Idade (dias)"], sa_dp["mean"], linewidth=2.2, marker="s", label="Média")
        _sdp = sa_dp.dropna(subset=["std"]).copy()
        if not _sdp.empty:
            ax.fill_between(_sdp["Idade (dias)"], _sdp["mean"] - _sdp["std"], _sdp["mean"] + _sdp["std"], alpha=0.2, label="±1 DP")
        if fck_active is not None:
            ax.axhline(fck_active, linestyle=":", linewidth=2, color="#ef4444", label=f"fck projeto ({fck_active:.1f} MPa)")
        ax.set_xlabel("Idade (dias)"); ax.set_ylabel("Resistência (MPa)")
        ax.set_title("Crescimento da resistência por corpo de prova")
        place_right_legend(ax)
        ax.grid(True, linestyle="--", alpha=0.35); ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        render_screen_chart(fig1, "Crescimento da resistência", "Evolução real por corpo de prova ao longo das idades.", "GRÁFICO 1")
        if CAN_EXPORT:
            _buf1 = io.BytesIO(); fig1.savefig(_buf1, format="png", dpi=200, bbox_inches="tight")
            st.download_button("🖼️ Baixar Gráfico 1 (PNG)", data=_buf1.getvalue(), file_name="grafico1_real.png", mime="image/png")

        # === Gráfico 2 — curva estimada
        fig2, est_df = None, None
        fck28 = df_plot.loc[df_plot["Idade (dias)"] == 28, "Resistência (MPa)"].mean()
        fck7  = df_plot.loc[df_plot["Idade (dias)"] == 7,  "Resistência (MPa)"].mean()
        if pd.notna(fck28):
            est_df = pd.DataFrame({"Idade (dias)": [7, 28, 63], "Resistência (MPa)": [fck28*0.65, fck28, fck28*1.15]})
        elif pd.notna(fck7):
            _f28 = fck7 / 0.70
            est_df = pd.DataFrame({"Idade (dias)": [7, 28, 63], "Resistência (MPa)": [float(fck7), float(_f28), float(_f28)*1.15]})
        if est_df is not None:
            fig2, ax2 = plt.subplots(figsize=(7.8, 4.8))
            ax2.plot(est_df["Idade (dias)"], est_df["Resistência (MPa)"], linestyle="--", marker="o", linewidth=2, label="Curva Estimada")
            for x, y in zip(est_df["Idade (dias)"], est_df["Resistência (MPa)"]):
                ax2.text(x, y, f"{y:.1f}", ha="center", va="bottom", fontsize=9)
            ax2.set_title("Curva estimada")
            ax2.set_xlabel("Idade (dias)"); ax2.set_ylabel("Resistência (MPa)")
            place_right_legend(ax2); ax2.grid(True, linestyle="--", alpha=0.5)
            render_screen_chart(fig2, "Curva estimada", "Referência técnica calculada a partir dos dados disponíveis.", "GRÁFICO 2")
            if CAN_EXPORT:
                _buf2 = io.BytesIO(); fig2.savefig(_buf2, format="png", dpi=200, bbox_inches="tight")
                st.download_button("🖼️ Baixar Gráfico 2 (PNG)", data=_buf2.getvalue(), file_name="grafico2_estimado.png", mime="image/png")
        else:
            st.info("Não foi possível calcular a curva estimada (sem médias em 7 ou 28 dias).")

        # === Gráfico 3 — comparações
        fig3, cond_df, verif_fck_df = None, None, None
        mean_by_age = df_plot.groupby("Idade (dias)")["Resistência (MPa)"].mean()
        m1  = mean_by_age.get(1,  float("nan"))
        m3  = mean_by_age.get(3,  float("nan"))
        m7  = mean_by_age.get(7,  float("nan"))
        m14 = mean_by_age.get(14, float("nan"))
        m21 = mean_by_age.get(21, float("nan"))
        m28 = mean_by_age.get(28, float("nan"))
        m56 = mean_by_age.get(56, float("nan"))
        m63 = mean_by_age.get(63, float("nan"))

        verif_fck_df = pd.DataFrame({
            "Idade (dias)": [1, 3, 7, 14, 21, 28, 56, 63],
            "Média Real (MPa)": [m1, m3, m7, m14, m21, m28, m56, m63],
            "fck Projeto (MPa)": [
                float("nan"),
                float("nan"),
                (fck_active if fck_active is not None else float("nan")),
                (fck_active if fck_active is not None else float("nan")),
                (fck_active if fck_active is not None else float("nan")),
                (fck_active if fck_active is not None else float("nan")),
                (fck_active if fck_active is not None else float("nan")),
                (fck_active if fck_active is not None else float("nan")),
            ],
        })

        if est_df is not None:
            sa = stats_all_focus.copy(); sa["std"] = sa["std"].fillna(0.0)
            fig3, ax3 = plt.subplots(figsize=(9.6, 4.9))
            ax3.plot(sa["Idade (dias)"], sa["mean"], marker="s", linewidth=2, label=("Média (CP focado)" if cp_focus else "Média Real"))
            _sa_dp = sa[sa["count"] >= 2].copy()
            if not _sa_dp.empty:
                ax3.fill_between(_sa_dp["Idade (dias)"], _sa_dp["mean"] - _sa_dp["std"], _sa_dp["mean"] + _sa_dp["std"], alpha=0.2, label="Real ±1 DP")
            ax3.plot(est_df["Idade (dias)"], est_df["Resistência (MPa)"], linestyle="--", marker="o", linewidth=2, label="Estimado")
            if fck_active is not None:
                ax3.axhline(fck_active, linestyle=":", linewidth=2, color="#ef4444", label=f"fck projeto ({fck_active:.1f} MPa)")
            ax3.set_xlabel("Idade (dias)"); ax3.set_ylabel("Resistência (MPa)")
            ax3.set_title("Comparação Real × Estimado (médias)")
            place_right_legend(ax3); ax3.grid(True, linestyle="--", alpha=0.5)
            render_screen_chart(fig3, "Comparação Real × Estimado", "Médias reais comparadas com a curva de referência.", "GRÁFICO 3")
            if CAN_EXPORT:
                _buf3 = io.BytesIO(); fig3.savefig(_buf3, format="png", dpi=200, bbox_inches="tight")
                st.download_button("🖼️ Baixar Gráfico 3 (PNG)", data=_buf3.getvalue(), file_name="grafico3_comparacao.png", mime="image/png")

            def _status_row(delta, tol):
                if pd.isna(delta): return "⚪ Sem dados"
                if abs(delta) <= tol: return "✅ Dentro"
                return "🔵 Acima" if delta > 0 else "🔴 Abaixo"

            _TOL = float(s["TOL_MP"])
            cond_df = pd.DataFrame({
                "Idade (dias)": [7, 28, 63],
                "Média Real (MPa)": [
                    sa.loc[sa["Idade (dias)"] == 7,  "mean"].mean(),
                    sa.loc[sa["Idade (dias)"] == 28, "mean"].mean(),
                    sa.loc[sa["Idade (dias)"] == 63, "mean"].mean(),
                ],
                "Estimado (MPa)": est_df.set_index("Idade (dias)")["Resistência (MPa)"].reindex([7, 28, 63]).values
            })
            cond_df["Δ (Real-Est.)"] = cond_df["Média Real (MPa)"] - cond_df["Estimado (MPa)"]
            cond_df["Status"] = [_status_row(d, _TOL) for d in cond_df["Δ (Real-Est.)"]]
            render_screen_table(cond_df, "Condição Real × Estimado", "Resumo das diferenças entre média real e curva estimada.")
        else:
            st.info("Sem curva estimada → não é possível comparar médias (Gráfico 3).")

        # === Gráfico 4 — pareamento ponto-a-ponto (melhorado)
        fig4, pareamento_df = None, None
        if est_df is not None and not est_df.empty:
            est_map = dict(zip(est_df["Idade (dias)"], est_df["Resistência (MPa)"]))
            pares = []
            fig4, ax4 = plt.subplots(figsize=(10.2, 5.0))
            for cp, sub in df_plot.groupby("CP"):
                sub = sub.sort_values("Idade (dias)")
                ax4.plot(sub["Idade (dias)"], sub["Resistência (MPa)"], marker="o", linewidth=1.6, label=f"CP {cp} — Real")
                x_est = []; y_est = []
                for _, r in sub.iterrows():
                    idade = int(r["Idade (dias)"])
                    if idade in est_map:
                        x_est.append(idade); y_est.append(float(est_map[idade]))
                        real = float(r["Resistência (MPa)"]); estv = float(est_map[idade])
                        delta = real - estv
                        _TOL = float(s["TOL_MP"])
                        status = "✅ OK" if abs(delta) <= _TOL else ("🔵 Acima" if delta > 0 else "🔴 Abaixo")
                        pares.append([str(cp), idade, real, estv, delta, status])
                        ax4.vlines(idade, min(real, estv), max(real, estv), linestyles=":", linewidth=1)
                if x_est:
                    ax4.plot(x_est, y_est, marker="^", linestyle="--", linewidth=1.6, label=f"CP {cp} — Est.")
            if fck_active is not None:
                ax4.axhline(fck_active, linestyle=":", linewidth=2, color="#ef4444", label=f"fck projeto ({fck_active:.1f} MPa)")
            ax4.set_xlabel("Idade (dias)"); ax4.set_ylabel("Resistência (MPa)")
            ax4.set_title("Pareamento Real × Estimado por CP (Curva de Crescimento)")
            place_right_legend(ax4); ax4.grid(True, linestyle="--", alpha=0.5)
            render_screen_chart(fig4, "Real × Estimado ponto a ponto", "Diferença entre cada leitura real e sua referência estimada.", "GRÁFICO 4")
            if CAN_EXPORT:
                _buf4 = io.BytesIO(); fig4.savefig(_buf4, format="png", dpi=200, bbox_inches="tight")
                st.download_button("🖼️ Baixar Gráfico 4 (PNG)", data=_buf4.getvalue(), file_name="grafico4_pareamento.png", mime="image/png")
            pareamento_df = pd.DataFrame(pares, columns=["CP","Idade (dias)","Real (MPa)","Estimado (MPa)","Δ","Status"]).sort_values(["CP","Idade (dias)"])
            render_screen_table(pareamento_df, "Pareamento ponto a ponto", "Detalhamento das diferenças por CP e idade.")
        else:
            st.info("Sem curva estimada → não é possível parear pontos (Gráfico 4).")

        # ---------------------------------------------------------------
        # SEÇÃO 3 — verificação do fck (USANDO df_view para médias por idade)
        # ---------------------------------------------------------------
        st.markdown("<div id='fck-verification'></div>", unsafe_allow_html=True)
        st.markdown(_ui_section("Verificação do FCK", "Situação por corpo de prova e idade, considerando a regra de aprovação pelo melhor resultado do par.", "✅", "CONTROLE"), unsafe_allow_html=True)

        # usa o conjunto filtrado completo (df_view), não o df_plot
        fck_series_all = pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
        fck_active2 = float(fck_series_all.mode().iloc[0]) if not fck_series_all.empty else None

        # MÉDIAS POR IDADE EM CIMA DE TODOS OS CPs VISÍVEIS
        mean_by_age_all = df_view.groupby("Idade (dias)")["Resistência (MPa)"].mean()

        # inclui somente as idades que existirem no certificado, mantendo a ordem padrão
        idades_padrao = [1, 3, 7, 14, 21, 28, 56, 63]
        try:
            idades_existentes = set(pd.to_numeric(df_view["Idade (dias)"], errors="coerce").dropna().astype(int).tolist())
            idades_verif = [a for a in idades_padrao if a in idades_existentes]
            if not idades_verif:
                idades_verif = [28, 63]
        except Exception:
            idades_verif = [28, 63]

        medias = [mean_by_age_all.get(a, float("nan")) for a in idades_verif]
        fck_col = [
            (float("nan") if a in (1, 3) else (fck_active2 if fck_active2 is not None else float("nan")))
            for a in idades_verif
        ]

        verif_fck_df2 = pd.DataFrame({
            "Idade (dias)": idades_verif,
            "Média Real (MPa)": medias,
            "fck Projeto (MPa)": fck_col,
        })


        pass28_any = None
        try:
            if fck_active2 is not None:
                s28 = pd.to_numeric(df_view.loc[df_view["Idade (dias)"] == 28, "Resistência (MPa)"], errors="coerce").dropna()
                pass28_any = (bool((s28 >= float(fck_active2)).any()) if not s28.empty else None)
        except Exception:
            pass28_any = None

        resumo_status = []
        for idade, media, fckp in verif_fck_df2.itertuples(index=False):
            if pd.isna(media) or (pd.isna(fckp) and idade not in (1, 3)):
                resumo_status.append("⚪ Sem dados")
            else:
                if idade in (1, 3, 7, 14, 21):
                    resumo_status.append("🟡 Coletando dados")
                else:
                    if idade == 28:
                        if pass28_any is None:
                            resumo_status.append("⚪ Sem dados")
                        else:
                            resumo_status.append("🟢 Atingiu fck" if pass28_any else "🔴 Não atingiu fck")
                    else:
                        resumo_status.append("🟢 Atingiu fck" if float(media) >= float(fckp) else "🔴 Não atingiu fck")
        verif_fck_df2["Status"] = resumo_status
        with st.expander("📋 Resumo técnico por idade", expanded=False):
            render_screen_table(verif_fck_df2, "Resumo por idade", "Média real comparada ao FCK de projeto.")

        # detalhado por CP — incluindo 1, 3, 7, 14, 21, 28, 56 e 63 dias
        idades_interesse = [1, 3, 7, 14, 21, 28, 56, 63]
        tmp_v = df_view[df_view["Idade (dias)"].isin(idades_interesse)].copy()
        pv_cp_status = None
        if tmp_v.empty:
            st.info("Sem CPs de 1/3/7/14/21/28/56/63 dias no filtro atual.")
        else:
            tmp_v["MPa"] = pd.to_numeric(tmp_v["Resistência (MPa)"], errors="coerce")
            tmp_v["rep"] = tmp_v.groupby(["CP", "Idade (dias)"]).cumcount() + 1
            pv_multi = tmp_v.pivot_table(
                index="CP",
                columns=["Idade (dias)", "rep"],
                values="MPa",
                aggfunc="first"
            ).sort_index(axis=1)

            for age in idades_interesse:
                if age not in pv_multi.columns.get_level_values(0):
                    pv_multi[(age, 1)] = pd.NA

            def _flat(age, rep):
                base = f"{age}d"
                return f"{base} (MPa)" if rep == 1 else f"{base} #{rep} (MPa)"

            pv = pv_multi.copy()
            pv.columns = [_flat(a, r) for (a, r) in pv_multi.columns]
            pv = pv.reset_index()
            try:
                pv["__cp_sort__"] = pv["CP"].astype(str).str.extract(r"(\d+)").astype(float)
            except Exception:
                pv["__cp_sort__"] = range(len(pv))
            pv = pv.sort_values(["__cp_sort__", "CP"]).drop(columns="__cp_sort__", errors="ignore")

            # status columns por idade
            def _status_text_media(media_idade, age, fckp):
                if pd.isna(media_idade):
                    return "⚪ Sem dados"
                if age in (1, 3, 7, 14, 21):
                    return "🟡 Coletando dados"
                if (fckp is None) or pd.isna(fckp):
                    return "⚪ Sem dados"
                return "🟢 Atingiu fck" if float(media_idade) >= float(fckp) else "🔴 Não atingiu fck"

            media_by_age = {}
            for age in idades_interesse:
                if age in pv_multi.columns.get_level_values(0):
                    # Para as idades de verificação final, basta 1 resultado do par atingir o fck.
                    media_by_age[age] = (
                        pv_multi[age].max(axis=1)
                        if age in (28, 56, 63)
                        else pv_multi[age].mean(axis=1)
                    )
                else:
                    media_by_age[age] = pd.Series(pd.NA, index=pv_multi.index)

            status_df = pd.DataFrame(index=pv_multi.index)
            for age in idades_interesse:
                colname = f"Status {age}d"
                status_df[colname] = [
                    _status_text_media(media_by_age[age].reindex(pv_multi.index).iloc[i], age, fck_active2)
                    for i in range(len(pv_multi.index))
                ]

            # ===============================================================
            # ALERTA DE CONSISTÊNCIA DOS PARES
            # ===============================================================
            # Mantém a regra original: diferença entre o maior e o menor
            # resultado da MESMA idade > 2,0 MPa gera alerta.
            #
            # Além do sinalizador simples (usado também no PDF), montamos
            # dados detalhados para a tela: CP, idade, menor/maior resultado
            # e o delta exato. O mapeamento é feito por CP para não correr
            # risco de desalinhamento após a ordenação numérica dos CPs.
            LIMITE_DELTA_PAR_MPA = 2.0

            alerta_por_cp = {}
            maior_delta_por_cp = {}
            detalhe_por_cp = {}
            alertas_detalhados = []

            for idx_ in pv_multi.index:
                deltas_validos = []
                detalhes_cp = []

                for age in idades_interesse:
                    cols = [c for c in pv_multi.columns if c[0] == age]
                    if not cols:
                        continue

                    vals = pd.to_numeric(
                        pd.Series(pv_multi.loc[idx_, cols]),
                        errors="coerce"
                    ).dropna().astype(float)

                    # Só existe comparação de "par" quando há pelo menos
                    # dois resultados válidos na mesma idade.
                    if len(vals) < 2:
                        continue

                    vmin = float(vals.min())
                    vmax = float(vals.max())
                    delta = vmax - vmin
                    deltas_validos.append(delta)

                    if delta > LIMITE_DELTA_PAR_MPA:
                        detalhes_cp.append(
                            f"{age}d: Δ {delta:.2f} MPa"
                        )
                        alertas_detalhados.append({
                            "CP": idx_,
                            "Idade (dias)": int(age),
                            "Menor resultado (MPa)": vmin,
                            "Maior resultado (MPa)": vmax,
                            "Δ do par (MPa)": delta,
                            "Situação": "🚨 REVISAR",
                        })

                tem_alerta = bool(detalhes_cp)
                alerta_por_cp[idx_] = "🟠 Δ pares > 2 MPa" if tem_alerta else ""
                maior_delta_por_cp[idx_] = max(deltas_validos) if deltas_validos else float("nan")
                detalhe_por_cp[idx_] = " • ".join(detalhes_cp) if detalhes_cp else "Dentro do limite"

            pv = pv.merge(status_df, left_on="CP", right_index=True, how="left")
            pv["Alerta Pares (Δ>2 MPa)"] = pv["CP"].map(alerta_por_cp).fillna("")

            # ordem de colunas — preserva a estrutura usada pelo PDF
            cols_cp = ["CP"]
            def _cols_age(age):
                base = [c for c in pv.columns if c.startswith(f"{age}d")]
                status_col = f"Status {age}d"
                if status_col in pv.columns:
                    base = base + [status_col]
                return base

            ordered_cols = (
                cols_cp
                + _cols_age(1)
                + _cols_age(3)
                + _cols_age(7)
                + _cols_age(14)
                + _cols_age(21)
                + _cols_age(28)
                + _cols_age(56)
                + _cols_age(63)
                + ["Alerta Pares (Δ>2 MPa)"]
            )
            pv = pv[ordered_cols]
            pv_cp_status = pv.copy()

            # O painel principal agora também mostra uma coluna "PARES"
            # e um KPI com a quantidade de CPs que exigem revisão.
            render_fck_dashboard(pv_cp_status, fck_active2)

            # ===============================================================
            # TABELA TÉCNICA / ALERTAS — DESTAQUE DE TELA
            # ===============================================================
            # Esta área fica visível logo abaixo do painel. Quando existe
            # problema, os detalhes são mostrados imediatamente e a tabela
            # completa abre automaticamente.
            alertas_df = pd.DataFrame(alertas_detalhados)
            if not alertas_df.empty:
                alertas_df = alertas_df.sort_values(
                    ["Δ do par (MPa)", "CP", "Idade (dias)"],
                    ascending=[False, True, True],
                    kind="stable"
                ).reset_index(drop=True)

            cps_com_alerta = int(alertas_df["CP"].nunique()) if not alertas_df.empty else 0
            ocorrencias_alerta = int(len(alertas_df))
            maior_delta_geral = (
                float(alertas_df["Δ do par (MPa)"].max())
                if not alertas_df.empty else 0.0
            )
            tem_alerta_par = ocorrencias_alerta > 0

            st.markdown("<div id='pair-analysis'></div>", unsafe_allow_html=True)
            st.markdown(
                _ui_section(
                    "Tabela técnica e consistência dos pares",
                    "Conferência dos resultados repetidos por CP e idade. Diferenças acima de 2,0 MPa são destacadas para revisão.",
                    "🚨" if tem_alerta_par else "🧪",
                    "REVISAR" if tem_alerta_par else "SEM ALERTAS"
                ),
                unsafe_allow_html=True
            )

            if tem_alerta_par:
                st.markdown(
                    f"""
                    <div style="
                        margin:2px 0 12px;padding:14px 16px;border-radius:14px;
                        border:1px solid rgba(239,68,68,.45);
                        background:linear-gradient(90deg,rgba(239,68,68,.16),rgba(249,115,22,.08));
                        box-shadow:0 8px 24px rgba(239,68,68,.08);
                    ">
                      <div style="font-size:16px;font-weight:950;color:#ef4444">
                        🚨 ATENÇÃO — diferença entre pares acima de 2,0 MPa
                      </div>
                      <div style="font-size:12px;color:var(--muted);margin-top:4px">
                        Encontrados <b>{cps_com_alerta} CP(s)</b> com <b>{ocorrencias_alerta} ocorrência(s)</b>.
                        Maior diferença observada: <b>{maior_delta_geral:.2f} MPa</b>.
                        Este alerta é de consistência do par e não altera sozinho a situação de atendimento ao FCK.
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    """
                    <div style="
                        margin:2px 0 12px;padding:13px 16px;border-radius:14px;
                        border:1px solid rgba(34,197,94,.35);
                        background:rgba(34,197,94,.08);
                    ">
                      <div style="font-size:15px;font-weight:900;color:#22c55e">
                        ✅ Pares conferidos — nenhuma diferença acima de 2,0 MPa
                      </div>
                      <div style="font-size:12px;color:var(--muted);margin-top:4px">
                        Não foram identificadas divergências de pares acima do limite no filtro atual.
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

            k1, k2, k3 = st.columns(3)
            k1.metric("CPs com alerta de pares", cps_com_alerta)
            k2.metric("Ocorrências Δ > 2 MPa", ocorrencias_alerta)
            k3.metric("Maior Δ encontrado", f"{maior_delta_geral:.2f} MPa" if tem_alerta_par else "≤ 2,00 MPa")

            if tem_alerta_par:
                alertas_view = alertas_df.copy()

                def _style_alerta_rows(row):
                    return [
                        "background-color:rgba(239,68,68,.10);font-weight:700;"
                        for _ in row.index
                    ]

                alerta_styler = (
                    alertas_view.style
                    .apply(_style_alerta_rows, axis=1)
                    .format({
                        "Menor resultado (MPa)": "{:.2f}",
                        "Maior resultado (MPa)": "{:.2f}",
                        "Δ do par (MPa)": "{:.2f}",
                    }, na_rep="—")
                )
                st.markdown(
                    "<div class='ui-table-title' style='margin-top:8px'>🚨 Ocorrências que exigem revisão</div>"
                    "<div class='ui-table-sub' style='margin-bottom:7px'>Veja primeiro estes CPs antes da tabela completa.</div>",
                    unsafe_allow_html=True
                )
                st.dataframe(
                    alerta_styler,
                    use_container_width=True,
                    hide_index=True,
                    height=min(120 + 35 * len(alertas_view), 430)
                )

            # Cópia exclusiva da tela: traz o alerta para as primeiras colunas
            # e mostra o maior delta encontrado por CP.
            pv_tecnica_screen = pv_cp_status.copy()
            pv_tecnica_screen["Situação dos Pares"] = pv_tecnica_screen["CP"].map(
                lambda cp: "🚨 REVISAR" if alerta_por_cp.get(cp, "") else "✅ OK"
            )
            pv_tecnica_screen["Maior Δ do Par (MPa)"] = pv_tecnica_screen["CP"].map(maior_delta_por_cp)
            pv_tecnica_screen["Detalhe Δ > 2 MPa"] = pv_tecnica_screen["CP"].map(detalhe_por_cp).fillna("Sem par disponível")

            # Remove da visualização a coluna antiga redundante e traz as
            # novas colunas de conferência para perto do CP.
            pv_tecnica_screen = pv_tecnica_screen.drop(
                columns=["Alerta Pares (Δ>2 MPa)"],
                errors="ignore"
            )
            front_cols = [
                "CP",
                "Situação dos Pares",
                "Maior Δ do Par (MPa)",
                "Detalhe Δ > 2 MPa",
            ]
            other_cols = [c for c in pv_tecnica_screen.columns if c not in front_cols]
            pv_tecnica_screen = pv_tecnica_screen[front_cols + other_cols]

            def _style_tech_rows(row):
                has_alert = str(row.get("Situação dos Pares", "")).startswith("🚨")
                if has_alert:
                    return [
                        "background-color:rgba(239,68,68,.10);"
                        + ("font-weight:800;" if col in ("CP", "Situação dos Pares", "Maior Δ do Par (MPa)", "Detalhe Δ > 2 MPa") else "")
                        for col in row.index
                    ]
                return ["" for _ in row.index]

            tech_styler = (
                pv_tecnica_screen.style
                .apply(_style_tech_rows, axis=1)
                .format({"Maior Δ do Par (MPa)": "{:.2f}"}, na_rep="—")
            )

            expander_label = (
                f"🚨 VER TABELA TÉCNICA COMPLETA — {cps_com_alerta} CP(s) COM ALERTA"
                if tem_alerta_par
                else "📋 VER TABELA TÉCNICA COMPLETA — PARES OK"
            )
            with st.expander(expander_label, expanded=tem_alerta_par):
                st.caption(
                    "Linhas destacadas em vermelho exigem revisão. "
                    "A coluna 'Detalhe Δ > 2 MPa' informa imediatamente a idade e a diferença encontrada."
                )
                st.dataframe(
                    tech_styler,
                    use_container_width=True,
                    hide_index=True,
                    height=min(180 + 34 * len(pv_tecnica_screen), 680)
                )


        # ---------------------------------------------------------------
        # SEÇÃO 4 — exportações
        # ---------------------------------------------------------------
        st.markdown(_ui_section("Exportações", "Gere arquivos e relatórios a partir do conjunto filtrado.", "⬇", "ARQUIVOS"), unsafe_allow_html=True)

        # checklist visual
        items = []
        items.append(("✅ Dados disponíveis", not df_view.empty))
        items.append(("✅ Sem falha de leitura", True))
        if has_nf_violation:
            items.append(("⚠️ Há Nota Fiscal em mais de um relatório", False))
        if has_cp_violation:
            items.append(("⚠️ Há CP em mais de um relatório", False))
        if multiple_fck_detected:
            items.append(("⚠️ Múltiplos fck detectados — filtrado para 1", True))
        _check_html = ['<div class="export-checks">']
        for label, ok in items:
            color = "#4ade80" if ok else "#fb923c"
            _check_html.append(f'<div class="export-check" style="color:{color}">{label}</div>')
        _check_html.append('</div>')
        st.markdown("".join(_check_html), unsafe_allow_html=True)

        report_mode = st.radio(
            "Modo do relatório PDF",
            [
                "Relatório técnico completo",
                "Relatório resumido (cliente)",
                "Conferência rápida (tabelas)"
            ],
            index=0,
            help="Escolha o nível de detalhe que vai para o PDF."
        )

        def gerar_pdf(df: pd.DataFrame, stats: pd.DataFrame, fig1, fig2, fig3, fig4,
                      obra_label: str, data_label: str, fck_label: str,
                      verif_fck_df: Optional[pd.DataFrame],
                      cond_df: Optional[pd.DataFrame],
                      pv_cp_status: Optional[pd.DataFrame],
                      responsavel: str,
                      cliente: str,
                      cidade: str,
                      report_mode: str) -> bytes:
            from reportlab.lib import colors as _C
            C = _C  # alias por compatibilidade (alguns trechos usam C)
            import tempfile, io

            # >>>>>> NOVO: modo básico interno
            is_basic = (report_mode == "__BASICO__")
            # ID do documento (estável para o mesmo conjunto de dados)
            try:
                _cp_key = "|".join(sorted(set(df["CP"].dropna().astype(str).tolist()))) if isinstance(df, pd.DataFrame) and "CP" in df.columns else ""
                _base_id = f"{obra_label}|{data_label}|{fck_label}|{len(df)}|{_cp_key}"
            except Exception:
                _base_id = f"{obra_label}|{data_label}|{fck_label}"
            doc_id_pdf = "HAB-" + hashlib.sha1(_base_id.encode("utf-8")).hexdigest()[:12].upper()

            # define que seções entram
            include_tables = True  # sempre
            include_graphs = (True if is_basic else report_mode in ("Relatório técnico completo", "Relatório resumido (cliente)"))
            include_verif  = (False if is_basic else report_mode in ("Relatório técnico completo",))
            include_cp_det = (True if is_basic else report_mode in ("Relatório técnico completo",))

            use_landscape = (len(df.columns) >= 8)
            pagesize = landscape(A4) if use_landscape else A4
            buffer = io.BytesIO()
            doc = SimpleDocTemplate(buffer, pagesize=pagesize, leftMargin=18, rightMargin=18, topMargin=26, bottomMargin=56)
            styles = getSampleStyleSheet()
            styles["Title"].fontName = "Helvetica-Bold";  styles["Title"].fontSize = 18
            styles["Heading2"].fontName = "Helvetica-Bold"; styles["Heading2"].fontSize = 14
            styles["Heading3"].fontName = "Helvetica-Bold"; styles["Heading3"].fontSize = 12
            styles["Normal"].fontName = "Helvetica"; styles["Normal"].fontSize = 9
            story = []

            story.append(Paragraph("<b>Habisolute Engenharia e Controle Tecnológico</b>", styles['Title']))
            story.append(Paragraph("Relatório Técnico de Rompimento de Corpos de Prova", styles['Heading2']))

            def _usina_label_from_df(df_: pd.DataFrame) -> str:
                if "Usina" not in df_.columns: return "—"
                seri = df_["Usina"].dropna().astype(str)
                if seri.empty: return "—"
                m = seri.mode()
                return str(m.iat[0]) if not m.empty else "—"

            def _abat_nf_header_label(df_: pd.DataFrame) -> str:
                snf = pd.to_numeric(df_.get("Abatimento NF (mm)"), errors="coerce").dropna()
                stol = pd.to_numeric(df_.get("Abatimento NF tol (mm)"), errors="coerce").dropna()
                if snf.empty: return "—"
                v = float(snf.mode().iloc[0]); t = float(stol.mode().iloc[0]) if not stol.empty else 0.0
                return f"{v:.0f} ± {t:.0f} mm"

            material_label, norma_label, dimensao_label = _resumo_material_norma_df(df)
            calibracao_label = _resumo_calibracao_df(df)

            story.append(Paragraph(f"Obra: {obra_label}", styles['Normal']))
            story.append(Paragraph(f"Período (datas dos certificados): {data_label}", styles['Normal']))
            story.append(Paragraph(f"fck de projeto: {fck_label}", styles['Normal']))
            story.append(Paragraph(f"Usina: {_usina_label_from_df(df)}", styles['Normal']))
            story.append(Paragraph(f"Abatimento de NF: {_abat_nf_header_label(df)}", styles['Normal']))
            story.append(Spacer(1, 8))

            from reportlab.lib.enums import TA_CENTER
            from reportlab.lib.styles import ParagraphStyle
            norma_title_style = ParagraphStyle(
                "norma_title_style",
                parent=styles["Normal"],
                fontName="Helvetica-Bold",
                fontSize=10.5,
                leading=13,
                alignment=TA_CENTER,
                textColor=colors.black,
            )
            norma_text_style = ParagraphStyle(
                "norma_text_style",
                parent=styles["Normal"],
                fontName="Helvetica-Bold",
                fontSize=9.2,
                leading=11,
                alignment=TA_CENTER,
                textColor=colors.black,
            )
            norma_small_style = ParagraphStyle(
                "norma_small_style",
                parent=styles["Normal"],
                fontName="Helvetica",
                fontSize=8.8,
                leading=10.5,
                alignment=TA_CENTER,
                textColor=colors.black,
            )
            norma_box = Table(
                [[Paragraph(f"Material: {material_label}", norma_text_style)],
                 [Paragraph(f"Calibração: {calibracao_label}", norma_small_style)],
                 [Paragraph(norma_label, norma_text_style)],
                 [Paragraph(f"Corpo de prova: {dimensao_label}", norma_small_style)]],
                colWidths=[doc.width]
            )
            norma_box.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF7ED')),
                ('BOX', (0, 0), (-1, -1), 1.0, colors.HexColor('#F97316')),
                ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#FDBA74')),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            story.append(norma_box)
            if cliente:
                story.append(Paragraph(f"Cliente / Empreendimento: {cliente}", styles['Normal']))
            if cidade:
                story.append(Paragraph(f"Cidade / UF: {cidade}", styles['Normal']))
            if responsavel:
                story.append(Paragraph(f"Responsável técnico: {responsavel}", styles['Normal']))
            story.append(Spacer(1, 8))

            # =========================
            # TABELA PRINCIPAL (AUTO-FIT + QUEBRA DE LINHA)
            # =========================
            if include_tables:
                from reportlab.lib.styles import ParagraphStyle
                from reportlab.lib.enums import TA_LEFT, TA_CENTER

                headers = ["Relatório","CP","Idade (dias)","Resistência (MPa)","Nota Fiscal","Local","Usina","Abatimento NF (mm)","Abatimento Obra (mm)","Arquivo"]
                df_tab = df.copy()
                for col in headers:
                    if col not in df_tab.columns:
                        df_tab[col] = ""

                # estilos (quebra automática dentro da célula)
                st_head = ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, leading=9, alignment=TA_CENTER)
                st_num  = ParagraphStyle("tn", fontName="Helvetica",      fontSize=7.2, leading=8, alignment=TA_CENTER)
                st_txt  = ParagraphStyle("tt", fontName="Helvetica",      fontSize=7.2, leading=8, alignment=TA_LEFT, wordWrap="CJK")

                def _esc(x):
                    s0 = "" if x is None else str(x)
                    if s0.lower() == "nan":
                        s0 = ""
                    return (s0.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                             .replace("\n", "<br/>"))

                def P(x, kind="txt"):
                    if kind == "head": return Paragraph(_esc(x), st_head)
                    if kind == "num":  return Paragraph(_esc(x), st_num)
                    return Paragraph(_esc(x), st_txt)

                usable = float(doc.width)  # largura útil (com margens)
                base = [46, 58, 44, 60, 62, None, 62, 72, 78, 96]  # Local = None
                fixed_sum = sum(w for w in base if w is not None)
                local_w = max(140.0, usable - fixed_sum)
                colWidths = [(local_w if w is None else float(w)) for w in base]

                total_w = sum(colWidths)
                if total_w > usable:
                    scale = usable / total_w
                    colWidths = [w * scale for w in colWidths]

                head_row = [P(h, "head") for h in headers]
                num_cols = {"Relatório","Idade (dias)","Resistência (MPa)","Abatimento NF (mm)","Abatimento Obra (mm)"}
                data_rows = []
                for row in df_tab[headers].values.tolist():
                    out = []
                    for h, v in zip(headers, row):
                        out.append(P(v, "num" if h in num_cols else "txt"))
                    data_rows.append(out)

                table = Table([head_row] + data_rows, colWidths=colWidths, repeatRows=1, splitByRow=1)
                table.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),_C.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.35,_C.black),
                    ("VALIGN",(0,0),(-1,-1),"TOP"),
                    ("LEFTPADDING",(0,0),(-1,-1),3),
                    ("RIGHTPADDING",(0,0),(-1,-1),3),
                    ("TOPPADDING",(0,0),(-1,-1),2),
                    ("BOTTOMPADDING",(0,0),(-1,-1),2),
                ]))
                story.append(table); story.append(Spacer(1, 8))

            # >>>>>> NOVO: no básico NÃO entra "Resumo Estatístico"
            if (not is_basic) and stats is not None and not stats.empty:
                stt = [["CP","Idade (dias)","Média","DP","n"]] + stats.values.tolist()
                story.append(Paragraph("Resumo Estatístico (Média + DP)", styles['Heading3']))
                t2 = Table(stt, repeatRows=1)
                t2.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),_C.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.5,_C.black),
                    ("ALIGN",(0,0),(-1,-1),"CENTER"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.6),
                ]))
                story.append(t2); story.append(Spacer(1, 10))

            def _img_from_fig_pdf(_fig, w=620, h=420):
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                _fig.savefig(tmp.name, dpi=200, bbox_inches="tight")
                return RLImage(tmp.name, width=w, height=h)

            # >>>>>> NOVO: no básico entra SÓ o Gráfico 1
            if include_graphs:
                if fig1:
                    story.append(_img_from_fig_pdf(fig1, w=640, h=430)); story.append(Spacer(1, 8))
                if not is_basic:
                    if fig2: story.append(_img_from_fig_pdf(fig2, w=600, h=400)); story.append(Spacer(1, 8))
                    if fig3: story.append(_img_from_fig_pdf(fig3, w=640, h=430)); story.append(Spacer(1, 8))
                    if fig4: story.append(_img_from_fig_pdf(fig4, w=660, h=440)); story.append(Spacer(1, 8))

            if include_verif and verif_fck_df is not None and not verif_fck_df.empty:
                story.append(PageBreak())
                story.append(Paragraph("Verificação do fck de Projeto (Resumo por idade)", styles["Heading3"]))
                rows_v = [["Idade (dias)","Média Real (MPa)","fck Projeto (MPa)","Status"]]
                for _, r in verif_fck_df.iterrows():
                    rows_v.append([
                        r["Idade (dias)"],
                        f"{r['Média Real (MPa)']:.3f}" if pd.notna(r['Média Real (MPa)']) else "—",
                        f"{r.get('fck Projeto (MPa)', float('nan')):.3f}" if pd.notna(r.get('fck Projeto (MPa)', float('nan'))) else "—",
                        r.get("Status","—")
                    ])
                tv = Table(rows_v, repeatRows=1)
                ts = [
                    ("BACKGROUND",(0,0),(-1,0),_C.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.5,_C.black),
                    ("ALIGN",(0,0),(-2,-1),"CENTER"),
                    ("ALIGN",(-1,1),(-1,-1),"LEFT"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.6),
                ]
                # colorir status
                for i, row in enumerate(rows_v[1:], start=1):
                    txt = str(row[3]).lower()
                    if "analisando" in txt:   ts.append(("BACKGROUND",(3,i),(3,i),_C.HexColor("#facc15")))
                    elif "não atingiu" in txt or "nao atingiu" in txt or "abaixo" in txt:
                        ts.append(("BACKGROUND",(3,i),(3,i),_C.HexColor("#ef4444")))
                    elif "atingiu" in txt or "dentro" in txt:
                        ts.append(("BACKGROUND",(3,i),(3,i),_C.HexColor("#16a34a")))
                    elif "acima" in txt:
                        ts.append(("BACKGROUND",(3,i),(3,i),_C.HexColor("#3b82f6")))
                    elif "sem dados" in txt:
                        ts.append(("BACKGROUND",(3,i),(3,i),_C.HexColor("#e5e7eb")))
                tv.setStyle(TableStyle(ts))
                story.append(tv); story.append(Spacer(1, 8))

            if include_verif and cond_df is not None and not cond_df.empty:
                story.append(Paragraph("Condição Real × Estimado (médias)", styles["Heading3"]))
                rows_c = [["Idade (dias)","Média Real (MPa)","Estimado (MPa)","Δ (Real-Est.)","Status"]]
                for _, r in cond_df.iterrows():
                    rows_c.append([
                        r["Idade (dias)"],
                        f"{r['Média Real (MPa)']:.3f}" if pd.notna(r['Média Real (MPa)']) else "—",
                        f"{r['Estimado (MPa)']:.3f}" if pd.notna(r['Estimado (MPa)']) else "—",
                        f"{r['Δ (Real-Est.)']:.3f}" if pd.notna(r['Δ (Real-Est.)']) else "—",
                        r["Status"]
                    ])
                tc = Table(rows_c, repeatRows=1)
                ts2 = [
                    ("BACKGROUND",(0,0),(-1,0),_C.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.5,_C.black),
                    ("ALIGN",(0,0),(-2,-1),"CENTER"),
                    ("ALIGN",(-1,1),(-1,-1),"LEFT"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.6),
                ]
                # colorir status
                for i, row in enumerate(rows_c[1:], start=1):
                    txt = str(row[4]).lower()
                    if "analisando" in txt:   ts2.append(("BACKGROUND",(4,i),(4,i),_C.HexColor("#facc15")))
                    elif "não atingiu" in txt or "nao atingiu" in txt or "abaixo" in txt:
                        ts2.append(("BACKGROUND",(4,i),(4,i),_C.HexColor("#ef4444")))
                    elif "atingiu" in txt or "dentro" in txt:
                        ts2.append(("BACKGROUND",(4,i),(4,i),_C.HexColor("#16a34a")))
                    elif "acima" in txt:
                        ts2.append(("BACKGROUND",(4,i),(4,i),_C.HexColor("#3b82f6")))
                    elif "sem dados" in txt:
                        ts2.append(("BACKGROUND",(4,i),(4,i),_C.HexColor("#e5e7eb")))
                tc.setStyle(TableStyle(ts2))
                story.append(tc); story.append(Spacer(1, 8))

            if include_cp_det and pv_cp_status is not None and not pv_cp_status.empty:
                story.append(PageBreak())
                story.append(Paragraph("Verificação detalhada por CP (1/3/7/14/21/28/56/63 dias)", styles["Heading3"]))

                det_df = pv_cp_status.copy()
                # No relatório básico, não exibir o campo/coluna de alerta de pares
                if is_basic:
                    cols_drop = [c for c in det_df.columns if ("Alerta Pares" in str(c)) or ("Δ>2" in str(c)) or ("Δ≥2" in str(c))]
                    if cols_drop:
                        det_df = det_df.drop(columns=cols_drop)

                # Colunas dinâmicas: só mostrar idades que tenham pelo menos 1 valor numérico (MPa)
                # (evita imprimir blocos totalmente vazios, mantendo o resto do relatório igual)
                def _has_numeric(_ser):
                    _s = pd.to_numeric(_ser, errors="coerce")
                    return _s.notna().any()

                cols = []
                if "CP" in det_df.columns:
                    cols.append("CP")

                age_order = [1, 3, 7, 14, 21, 28, 56, 63]

                # adiciona blocos de idades que existirem no PDF (MPa + Status)
                for _age in age_order:
                    mp_cols = [c for c in det_df.columns if str(c).startswith(f"{_age}d") and "(MPa)" in str(c)]
                    status_cols = [c for c in det_df.columns if str(c).strip().lower() == f"status {_age}d".lower()]

                    present = any(_has_numeric(det_df[c]) for c in mp_cols) if mp_cols else False
                    if present:
                        # adiciona somente colunas de MPa que tenham números (ex.: 28d #2 só quando existir)
                        for c in mp_cols:
                            if _has_numeric(det_df[c]):
                                cols.append(c)
                        for c in status_cols:
                            cols.append(c)

                # adiciona as demais colunas "fixas" na ordem original (ex.: Alerta Pares)
                age_like = set()
                for _age in age_order:
                    for c in det_df.columns:
                        sc = str(c)
                        if sc.startswith(f"{_age}d") and "(MPa)" in sc:
                            age_like.add(c)
                        if sc.strip().lower() == f"status {_age}d".lower():
                            age_like.add(c)

                for c in det_df.columns:
                    if c not in cols and c not in age_like:
                        cols.append(c)
                # reindexa para garantir que 'cols' esteja alinhado aos dados (evita list index out of range)
                cols = [c for c in cols if c in det_df.columns]
                if not cols:
                    cols = list(det_df.columns)
                det_df2 = det_df[cols].copy()
                # estilos (quebra automática + compactação p/ caber na página)
                from reportlab.lib.styles import ParagraphStyle
                from reportlab.lib.enums import TA_LEFT, TA_CENTER

                st_head = ParagraphStyle("th_det", fontName="Helvetica-Bold", fontSize=7.4, leading=8.4, alignment=TA_CENTER)
                st_num  = ParagraphStyle("tn_det", fontName="Helvetica",      fontSize=7.0, leading=8.0, alignment=TA_CENTER)
                st_txt  = ParagraphStyle("tt_det", fontName="Helvetica",      fontSize=7.0, leading=8.0, alignment=TA_LEFT, wordWrap="CJK")

                def _esc(x):
                    s0 = "" if x is None else str(x)
                    return (s0.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

                def _cell(v, colname: str):
                    # mantém "—" para vazios
                    if v is None or (isinstance(v, float) and (pd.isna(v))):
                        v = "—"
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        txt = f"{float(v):.2f}".rstrip("0").rstrip(".")
                    else:
                        txt = str(v)
                    # status e textos longos: quebra; numéricos: centraliza
                    if "Status" in colname or colname == "CP":
                        style = st_txt if "Status" in colname else st_num
                        return Paragraph(_esc(txt), style)
                    if "(MPa)" in colname or colname.endswith("d") or "Idade" in colname:
                        return Paragraph(_esc(txt), st_num)
                    return Paragraph(_esc(txt), st_txt)

                # colWidths proporcionais (evita desconfigurar a última página)
                avail_w = pagesize[0] - doc.leftMargin - doc.rightMargin
                weights = []
                for c in cols:
                    if c == "CP":
                        weights.append(1.2)
                    elif "Status" in c:
                        weights.append(1.7)
                    else:
                        weights.append(1.0)
                tot = sum(weights) if weights else 1.0
                colWidths = [max(28.0, avail_w * (w / tot)) for w in weights]

                head_row = [Paragraph(_esc(c), st_head) for c in cols]
                data_rows = []
                for row in det_df2.values.tolist():
                    data_rows.append([_cell(v, cols[i]) for i, v in enumerate(row)])

                tab = [head_row] + data_rows
                t_det = Table(tab, colWidths=colWidths, repeatRows=1, splitByRow=1)
                ts = [
                    ("BACKGROUND",(0,0),(-1,0),C.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.35,C.black),
                    ("VALIGN",(0,0),(-1,-1),"TOP"),
                    ("LEFTPADDING",(0,0),(-1,-1),2),
                    ("RIGHTPADDING",(0,0),(-1,-1),2),
                    ("TOPPADDING",(0,0),(-1,-1),1),
                    ("BOTTOMPADDING",(0,0),(-1,-1),1),
                ]

                # destaca status (apenas colunas Status)
                for r_i, row in enumerate(det_df2.values.tolist(), start=1):
                    for c_i, col_name in enumerate(cols):
                        if "Status" not in col_name:
                            continue
                        txt = str(row[c_i]).lower()
                        if "analisando" in txt or "coletando" in txt:
                            ts.append(("BACKGROUND",(c_i,r_i),(c_i,r_i),C.HexColor("#facc15")))
                        elif "não atingiu" in txt or "nao atingiu" in txt or "abaixo" in txt:
                            ts.append(("BACKGROUND",(c_i,r_i),(c_i,r_i),C.HexColor("#ef4444")))
                        elif "atingiu" in txt or "dentro" in txt:
                            ts.append(("BACKGROUND",(c_i,r_i),(c_i,r_i),C.HexColor("#16a34a")))
                        elif "acima" in txt:
                            ts.append(("BACKGROUND",(c_i,r_i),(c_i,r_i),C.HexColor("#3b82f6")))
                        elif "sem dados" in txt:
                            ts.append(("BACKGROUND",(c_i,r_i),(c_i,r_i),C.HexColor("#e5e7eb")))

                t_det.setStyle(TableStyle(ts))
                story.append(t_det); story.append(Spacer(1, 6))

            story.append(Spacer(1, 10))
            story.append(Paragraph(f"<b>ID do documento:</b> {doc_id_pdf}", styles["Normal"]))

            story.extend(_qr_area_cliente_flowables(styles))
            doc.build(story, canvasmaker=NumberedCanvas)
            pdf = buffer.getvalue()
            buffer.close()
            return pdf

        def _date_label_from_df_pdf(df_: pd.DataFrame) -> str:
            try:
                _d = [_to_date_obj(x) for x in df_["Data Certificado"].dropna().tolist()]
                _d = [x for x in _d if x is not None]
                if not _d:
                    return "—"
                return min(_d).strftime('%d/%m/%Y') if min(_d) == max(_d) else f"{min(_d).strftime('%d/%m/%Y')} — {max(_d).strftime('%d/%m/%Y')}"
            except Exception:
                return "—"

        def _obra_label_from_df_pdf(df_: pd.DataFrame) -> str:
            try:
                if "Obra" in df_.columns and not df_["Obra"].dropna().empty:
                    return str(df_["Obra"].mode().iat[0])
            except Exception:
                pass
            return "—"

        def _stats_cp_idade_pdf(df_: pd.DataFrame) -> pd.DataFrame:
            if df_ is None or df_.empty:
                return pd.DataFrame(columns=["CP", "Idade (dias)", "Média", "Desvio_Padrão", "n"])
            return (
                df_.groupby(["CP", "Idade (dias)"])["Resistência (MPa)"]
                   .agg(Média="mean", Desvio_Padrão="std", n="count")
                   .reset_index()
            )

        def _fig_crescimento_pdf(df_: pd.DataFrame, fck_val: Optional[float]):
            try:
                fig, ax = plt.subplots(figsize=(9.6, 4.9))
                dfg = df_.copy()
                dfg["Idade (dias)"] = pd.to_numeric(dfg["Idade (dias)"], errors="coerce")
                dfg["Resistência (MPa)"] = pd.to_numeric(dfg["Resistência (MPa)"], errors="coerce")
                dfg = dfg.dropna(subset=["Idade (dias)", "Resistência (MPa)"])
                for cp, sub in dfg.groupby("CP"):
                    sub = sub.sort_values("Idade (dias)")
                    ax.plot(sub["Idade (dias)"], sub["Resistência (MPa)"], marker="o", linewidth=1.8, label=f"CP {cp}")
                if not dfg.empty:
                    media = dfg.groupby("Idade (dias)")["Resistência (MPa)"].mean().reset_index()
                    ax.plot(media["Idade (dias)"], media["Resistência (MPa)"], marker="s", linewidth=2.2, label="Média")
                if fck_val is not None and not pd.isna(fck_val):
                    ax.axhline(float(fck_val), linestyle=":", linewidth=2, color="#ef4444", label=f"fck projeto ({float(fck_val):.1f} MPa)")
                ax.set_title("Crescimento da resistência por corpo de prova")
                ax.set_xlabel("Idade (dias)")
                ax.set_ylabel("Resistência (MPa)")
                ax.grid(True, linestyle="--", alpha=0.35)
                try:
                    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
                except Exception:
                    pass
                place_right_legend(ax)
                return fig
            except Exception:
                return None

        def _verif_fck_pdf(df_: pd.DataFrame, fck_val: Optional[float]) -> pd.DataFrame:
            idades_ordem = [1, 3, 7, 14, 21, 28, 56, 63]
            if df_ is None or df_.empty:
                return pd.DataFrame(columns=["Idade (dias)", "Média Real (MPa)", "fck Projeto (MPa)", "Status"])
            mean_by_age = df_.groupby("Idade (dias)")["Resistência (MPa)"].mean()
            idades_exist = [a for a in idades_ordem if a in set(pd.to_numeric(df_["Idade (dias)"], errors="coerce").dropna().astype(int).tolist())]
            rows = []
            for age in idades_exist:
                media = mean_by_age.get(age, float("nan"))
                fck_col = float("nan") if age in (1, 3) else (float(fck_val) if fck_val is not None and not pd.isna(fck_val) else float("nan"))
                if age in (1, 3, 7, 14, 21):
                    status = "🟡 Coletando dados"
                elif pd.isna(media) or pd.isna(fck_col):
                    status = "⚪ Sem dados"
                else:
                    status = "🟢 Atingiu fck" if float(media) >= float(fck_col) else "🔴 Não atingiu fck"
                rows.append({"Idade (dias)": age, "Média Real (MPa)": media, "fck Projeto (MPa)": fck_col, "Status": status})
            return pd.DataFrame(rows)

        def _pv_cp_status_pdf(df_: pd.DataFrame, fck_val: Optional[float]) -> pd.DataFrame:
            idades_interesse = [1, 3, 7, 14, 21, 28, 56, 63]
            tmp_v = df_[df_["Idade (dias)"].isin(idades_interesse)].copy()
            if tmp_v.empty:
                return pd.DataFrame()
            tmp_v["MPa"] = pd.to_numeric(tmp_v["Resistência (MPa)"], errors="coerce")
            tmp_v["rep"] = tmp_v.groupby(["CP", "Idade (dias)"]).cumcount() + 1
            pv_multi = tmp_v.pivot_table(index="CP", columns=["Idade (dias)", "rep"], values="MPa", aggfunc="first").sort_index(axis=1)
            for age in idades_interesse:
                if age not in pv_multi.columns.get_level_values(0):
                    pv_multi[(age, 1)] = pd.NA
            pv_multi = pv_multi.sort_index(axis=1)

            def _flat(age, rep):
                base = f"{int(age)}d"
                return f"{base} (MPa)" if int(rep) == 1 else f"{base} #{int(rep)} (MPa)"

            pv = pv_multi.copy()
            pv.columns = [_flat(a, r) for (a, r) in pv_multi.columns]
            pv = pv.reset_index()
            try:
                pv["__cp_sort__"] = pv["CP"].astype(str).str.extract(r"(\d+)").astype(float)
                pv = pv.sort_values(["__cp_sort__", "CP"]).drop(columns="__cp_sort__", errors="ignore")
            except Exception:
                pass

            def _status_text(media_idade, age):
                if pd.isna(media_idade):
                    return "⚪ Sem dados"
                if age in (1, 3, 7, 14, 21):
                    return "🟡 Coletando dados"
                if fck_val is None or pd.isna(fck_val):
                    return "⚪ Sem dados"
                return "🟢 Atingiu fck" if float(media_idade) >= float(fck_val) else "🔴 Não atingiu fck"

            media_by_age = {}
            for age in idades_interesse:
                if age in pv_multi.columns.get_level_values(0):
                    # Para as idades de verificação final, basta 1 resultado do par atingir o fck.
                    media_by_age[age] = (
                        pv_multi[age].max(axis=1)
                        if age in (28, 56, 63)
                        else pv_multi[age].mean(axis=1)
                    )
                else:
                    media_by_age[age] = pd.Series(pd.NA, index=pv_multi.index)
            status_df = pd.DataFrame(index=pv_multi.index)
            for age in idades_interesse:
                status_df[f"Status {age}d"] = [_status_text(media_by_age[age].reindex(pv_multi.index).iloc[i], age) for i in range(len(pv_multi.index))]
            status_df = status_df.reset_index()
            pv = pv.merge(status_df, on="CP", how="left")

            def _cols_age(age):
                mp_cols = [c for c in pv.columns if str(c).startswith(f"{age}d") and "(MPa)" in str(c)]
                st_cols = [c for c in pv.columns if str(c).strip().lower() == f"status {age}d"]
                return mp_cols + st_cols
            ordered_cols = ["CP"] + _cols_age(1) + _cols_age(3) + _cols_age(7) + _cols_age(14) + _cols_age(21) + _cols_age(28) + _cols_age(56) + _cols_age(63)
            ordered_cols = [c for c in ordered_cols if c in pv.columns]
            return pv[ordered_cols]

        def gerar_pdf_agrupado_por_fck(df_base: pd.DataFrame, report_mode_atual: str) -> bytes:
            """Gera um único PDF com seções separadas por fck, sem depender do módulo pypdf."""
            from reportlab.lib import colors as _C
            from reportlab.lib.enums import TA_CENTER, TA_LEFT
            from reportlab.lib.styles import ParagraphStyle
            import io, tempfile

            if df_base is None or df_base.empty:
                return b""

            df_base = _atualizar_material_norma_linhas(df_base.copy())
            df_base["_FckLabel"] = df_base["Fck Projeto"].apply(_normalize_fck_label)
            fck_labels_group = [x for x in dict.fromkeys(df_base["_FckLabel"].tolist()) if str(x).strip() and str(x) != "—"]
            if not fck_labels_group:
                fck_labels_group = ["—"]

            def _sort_fck_label(lbl):
                try:
                    return float(str(lbl).replace(",", "."))
                except Exception:
                    return 10**9

            buffer = io.BytesIO()
            pagesize = landscape(A4)
            doc = SimpleDocTemplate(buffer, pagesize=pagesize, leftMargin=18, rightMargin=18, topMargin=26, bottomMargin=56)
            styles = getSampleStyleSheet()
            styles["Title"].fontName = "Helvetica-Bold";  styles["Title"].fontSize = 18
            styles["Heading2"].fontName = "Helvetica-Bold"; styles["Heading2"].fontSize = 14
            styles["Heading3"].fontName = "Helvetica-Bold"; styles["Heading3"].fontSize = 12
            styles["Normal"].fontName = "Helvetica"; styles["Normal"].fontSize = 9

            story = []
            story.append(Paragraph("<b>Habisolute Engenharia e Controle Tecnológico</b>", styles['Title']))
            story.append(Paragraph("Relatório Técnico de Rompimento de Corpos de Prova — Agrupado por fck", styles['Heading2']))
            story.append(Spacer(1, 8))

            norma_text_style = ParagraphStyle(
                "norma_text_style_grouped",
                parent=styles["Normal"],
                fontName="Helvetica-Bold",
                fontSize=9.2,
                leading=11,
                alignment=TA_CENTER,
                textColor=colors.black,
            )
            norma_small_style = ParagraphStyle(
                "norma_small_style_grouped",
                parent=styles["Normal"],
                fontName="Helvetica",
                fontSize=8.8,
                leading=10.5,
                alignment=TA_CENTER,
                textColor=colors.black,
            )
            st_head = ParagraphStyle("th_grouped", fontName="Helvetica-Bold", fontSize=7.4, leading=8.4, alignment=TA_CENTER)
            st_num  = ParagraphStyle("tn_grouped", fontName="Helvetica",      fontSize=7.0, leading=8.0, alignment=TA_CENTER)
            st_txt  = ParagraphStyle("tt_grouped", fontName="Helvetica",      fontSize=7.0, leading=8.0, alignment=TA_LEFT, wordWrap="CJK")

            def _esc(x):
                s0 = "" if x is None else str(x)
                if s0.lower() == "nan":
                    s0 = ""
                return (s0.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>"))

            def _cell(v, style=st_txt):
                return Paragraph(_esc(v), style)

            def _usina_label_from_df_group(df_: pd.DataFrame) -> str:
                if "Usina" not in df_.columns:
                    return "—"
                seri = df_["Usina"].dropna().astype(str)
                if seri.empty:
                    return "—"
                m = seri.mode()
                return str(m.iat[0]) if not m.empty else "—"

            def _abat_nf_header_label_group(df_: pd.DataFrame) -> str:
                snf = pd.to_numeric(df_.get("Abatimento NF (mm)"), errors="coerce").dropna()
                stol = pd.to_numeric(df_.get("Abatimento NF tol (mm)"), errors="coerce").dropna()
                if snf.empty:
                    return "—"
                v = float(snf.mode().iloc[0])
                if not stol.empty:
                    t = float(stol.mode().iloc[0])
                    return f"{v:.0f} ± {t:.0f} mm"
                return f"{v:.0f} mm"

            def _add_principal_table(df_g: pd.DataFrame):
                headers = ["Relatório", "CP", "Idade (dias)", "Resistência (MPa)", "Nota Fiscal", "Local", "Usina", "Abatimento NF (mm)", "Abatimento Obra (mm)", "Arquivo"]
                df_tab = df_g.copy()
                for col in headers:
                    if col not in df_tab.columns:
                        df_tab[col] = ""
                usable = float(doc.width)
                base = [46, 58, 44, 60, 62, None, 62, 72, 78, 96]
                fixed_sum = sum(w for w in base if w is not None)
                local_w = max(140.0, usable - fixed_sum)
                col_widths = [(local_w if w is None else float(w)) for w in base]
                total_w = sum(col_widths)
                if total_w > usable:
                    scale = usable / total_w
                    col_widths = [w * scale for w in col_widths]

                num_cols = {"Relatório", "Idade (dias)", "Resistência (MPa)", "Abatimento NF (mm)", "Abatimento Obra (mm)"}
                rows = [[_cell(h, st_head) for h in headers]]
                for row in df_tab[headers].values.tolist():
                    rows.append([_cell(v, st_num if h in num_cols else st_txt) for h, v in zip(headers, row)])
                t = Table(rows, colWidths=col_widths, repeatRows=1, splitByRow=1)
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (-1,0), _C.lightgrey),
                    ("GRID", (0,0), (-1,-1), 0.35, _C.black),
                    ("VALIGN", (0,0), (-1,-1), "TOP"),
                    ("LEFTPADDING", (0,0), (-1,-1), 2),
                    ("RIGHTPADDING", (0,0), (-1,-1), 2),
                    ("TOPPADDING", (0,0), (-1,-1), 1),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 1),
                ]))
                story.append(t)
                story.append(Spacer(1, 8))

            def _add_fig(fig):
                if fig is None:
                    return
                try:
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                    tmp.close()
                    fig.savefig(tmp.name, dpi=180, bbox_inches="tight")
                    img = RLImage(tmp.name)
                    max_w = doc.width * 0.88
                    max_h = 260
                    ratio = min(max_w / float(img.imageWidth), max_h / float(img.imageHeight))
                    img.drawWidth = float(img.imageWidth) * ratio
                    img.drawHeight = float(img.imageHeight) * ratio
                    story.append(img)
                    story.append(Spacer(1, 8))
                except Exception:
                    pass

            def _add_pv_table(pv_df: pd.DataFrame):
                if pv_df is None or pv_df.empty:
                    return
                det_df = pv_df.copy()
                cols = []
                if "CP" in det_df.columns:
                    cols.append("CP")
                age_order = [1, 3, 7, 14, 21, 28, 56, 63]
                def _has_numeric(_ser):
                    _s = pd.to_numeric(_ser, errors="coerce")
                    return _s.notna().any()
                for age in age_order:
                    mp_cols = [c for c in det_df.columns if str(c).startswith(f"{age}d") and "(MPa)" in str(c)]
                    status_cols = [c for c in det_df.columns if str(c).strip().lower() == f"status {age}d".lower()]
                    present = any(_has_numeric(det_df[c]) for c in mp_cols) if mp_cols else False
                    if present:
                        for c in mp_cols:
                            if _has_numeric(det_df[c]):
                                cols.append(c)
                        cols.extend(status_cols)
                if not cols:
                    cols = list(det_df.columns)
                cols = [c for c in cols if c in det_df.columns]
                det_df = det_df[cols].copy()

                def _format_value(v):
                    if v is None or (isinstance(v, float) and pd.isna(v)):
                        return "—"
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        return f"{float(v):.2f}".rstrip("0").rstrip(".")
                    return str(v)

                weights = []
                for c in cols:
                    if c == "CP": weights.append(1.2)
                    elif "Status" in str(c): weights.append(1.7)
                    else: weights.append(1.0)
                tot = sum(weights) if weights else 1.0
                col_widths = [max(28.0, doc.width * (w / tot)) for w in weights]

                rows = [[_cell(c, st_head) for c in cols]]
                for row in det_df.values.tolist():
                    out = []
                    for c, v in zip(cols, row):
                        style = st_txt if "Status" in str(c) else st_num
                        out.append(_cell(_format_value(v), style))
                    rows.append(out)
                t = Table(rows, colWidths=col_widths, repeatRows=1, splitByRow=1)
                ts = [
                    ("BACKGROUND", (0,0), (-1,0), _C.lightgrey),
                    ("GRID", (0,0), (-1,-1), 0.35, _C.black),
                    ("VALIGN", (0,0), (-1,-1), "TOP"),
                    ("LEFTPADDING", (0,0), (-1,-1), 2),
                    ("RIGHTPADDING", (0,0), (-1,-1), 2),
                    ("TOPPADDING", (0,0), (-1,-1), 1),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 1),
                ]
                for r_i, row in enumerate(det_df.values.tolist(), start=1):
                    for c_i, col_name in enumerate(cols):
                        if "Status" not in str(col_name):
                            continue
                        txt = str(row[c_i]).lower()
                        if "analisando" in txt or "coletando" in txt:
                            ts.append(("BACKGROUND", (c_i,r_i), (c_i,r_i), _C.HexColor("#facc15")))
                        elif "não atingiu" in txt or "nao atingiu" in txt or "abaixo" in txt:
                            ts.append(("BACKGROUND", (c_i,r_i), (c_i,r_i), _C.HexColor("#ef4444")))
                        elif "atingiu" in txt or "dentro" in txt:
                            ts.append(("BACKGROUND", (c_i,r_i), (c_i,r_i), _C.HexColor("#16a34a")))
                        elif "sem dados" in txt:
                            ts.append(("BACKGROUND", (c_i,r_i), (c_i,r_i), _C.HexColor("#e5e7eb")))
                t.setStyle(TableStyle(ts))
                story.append(t)
                story.append(Spacer(1, 8))

            figs_to_close = []
            first_group = True
            for lbl in sorted(fck_labels_group, key=_sort_fck_label):
                df_g = df_base[df_base["_FckLabel"] == lbl].drop(columns=["_FckLabel"], errors="ignore").copy()
                if df_g.empty:
                    continue
                df_g = _atualizar_material_norma_linhas(df_g)
                fck_g = _to_float_or_none(df_g["Fck Projeto"].mode().iat[0]) if "Fck Projeto" in df_g.columns and not df_g["Fck Projeto"].dropna().empty else None
                fck_label = _format_float_label(fck_g) if fck_g is not None else str(lbl)
                material_label, norma_label, dimensao_label = _resumo_material_norma_df(df_g)
                calibracao_label = _resumo_calibracao_df(df_g)

                if not first_group:
                    story.append(PageBreak())
                first_group = False

                story.append(Paragraph(f"<b>Grupo fck {fck_label} MPa</b>", styles["Heading2"]))
                story.append(Paragraph(f"Obra: {_obra_label_from_df_pdf(df_g)}", styles['Normal']))
                story.append(Paragraph(f"Período (datas dos certificados): {_date_label_from_df_pdf(df_g)}", styles['Normal']))
                story.append(Paragraph(f"fck de projeto: {fck_label}", styles['Normal']))
                story.append(Paragraph(f"Usina: {_usina_label_from_df_group(df_g)}", styles['Normal']))
                story.append(Paragraph(f"Abatimento de NF: {_abat_nf_header_label_group(df_g)}", styles['Normal']))
                story.append(Spacer(1, 6))

                norma_box = Table(
                    [[Paragraph(f"Material: {material_label}", norma_text_style)],
                     [Paragraph(f"Calibração: {calibracao_label}", norma_small_style)],
                     [Paragraph(norma_label, norma_text_style)],
                     [Paragraph(f"Corpo de prova: {dimensao_label}", norma_small_style)]],
                    colWidths=[doc.width]
                )
                norma_box.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF7ED')),
                    ('BOX', (0, 0), (-1, -1), 1.0, colors.HexColor('#F97316')),
                    ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#FDBA74')),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ]))
                story.append(norma_box)
                story.append(Spacer(1, 8))

                _add_principal_table(df_g)
                fig_g = _fig_crescimento_pdf(df_g, fck_g)
                if fig_g is not None:
                    figs_to_close.append(fig_g)
                _add_fig(fig_g)
                pv_g = _pv_cp_status_pdf(df_g, fck_g)
                if pv_g is not None and not pv_g.empty:
                    story.append(Paragraph("Verificação detalhada por CP (1/3/7/14/21/28/56/63 dias)", styles["Heading3"]))
                    _add_pv_table(pv_g)

            try:
                _cp_key = "|".join(sorted(set(df_base["CP"].dropna().astype(str).tolist()))) if "CP" in df_base.columns else ""
                _base_id = f"AGRUPADO|{len(df_base)}|{_cp_key}"
                doc_id_pdf = "HAB-" + hashlib.sha1(_base_id.encode("utf-8")).hexdigest()[:12].upper()
                story.append(Spacer(1, 10))
                story.append(Paragraph(f"<b>ID do documento:</b> {doc_id_pdf}", styles["Normal"]))
            except Exception:
                pass

            story.extend(_qr_area_cliente_flowables(styles))
            doc.build(story, canvasmaker=NumberedCanvas)
            for fg in figs_to_close:
                try:
                    plt.close(fg)
                except Exception:
                    pass
            pdf = buffer.getvalue()
            buffer.close()
            return pdf

        has_df = isinstance(df_view, pd.DataFrame) and (not df_view.empty)
        if has_df and CAN_EXPORT:
            try:
                pdf_bytes = gerar_pdf(
                    df_view, stats_cp_idade,
                    fig1 if 'fig1' in locals() else None,
                    fig2 if 'fig2' in locals() else None,
                    fig3 if 'fig3' in locals() else None,
                    fig4 if 'fig4' in locals() else None,
                    str(df_view["Obra"].mode().iat[0]) if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty else "—",
                    (lambda _d: (
                        (min(_d).strftime('%d/%m/%Y') if min(_d) == max(_d) else f"{min(_d).strftime('%d/%m/%Y')} — {max(_d).strftime('%d/%m/%Y')}")
                        if _d else "—"
                    ))([_to_date_obj(x) for x in df_view["Data Certificado"].dropna().tolist()]),
                    _format_float_label(fck_active) if 'fck_active' in locals() and fck_active is not None else "—",
                    verif_fck_df2 if 'verif_fck_df2' in locals() else None,
                    cond_df if 'cond_df' in locals() else None,
                    pv_cp_status if 'pv_cp_status' in locals() else None,
                    s.get("rt_responsavel",""),
                    s.get("rt_cliente",""),
                    s.get("rt_cidade",""),
                    report_mode,
                )

                file_name_pdf = build_pdf_filename(df_view, uploaded_files)
                st.download_button(
                    "📄 Baixar Relatório (PDF)",
                    data=pdf_bytes,
                    file_name=file_name_pdf,
                    mime="application/pdf",
                    use_container_width=True
                )
                log_event("export_pdf", {
                    "rows": int(df_view.shape[0]),
                    "relatorios": int(df_view["Relatório"].nunique()),
                    "obra": str(df_view["Obra"].mode().iat[0]) if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty else "—",
                    "file_name": file_name_pdf,
                    "mode": report_mode,
                })
                if pdf_bytes:
                    try: render_print_block(pdf_bytes, None, brand, brand600)
                    except Exception: pass

                # ============================================================
                # NOVO: Botão de PDF AGRUPADO POR FCK (um único PDF com seções por fck)
                # ============================================================
                try:
                    df_agrupado_base = df.loc[mask].drop(columns=["_DataObj"], errors="ignore").copy()
                    if isinstance(df_agrupado_base, pd.DataFrame) and not df_agrupado_base.empty:
                        pdf_agrupado_bytes = gerar_pdf_agrupado_por_fck(df_agrupado_base, report_mode)
                        file_name_agrupado = build_pdf_filename(df_agrupado_base, uploaded_files)
                        if file_name_agrupado.lower().endswith(".pdf"):
                            file_name_agrupado = file_name_agrupado[:-4] + "_AGRUPADO_POR_FCK.pdf"
                        else:
                            file_name_agrupado = file_name_agrupado + "_AGRUPADO_POR_FCK.pdf"
                        st.download_button(
                            "📚 Baixar PDF agrupado por fck",
                            data=pdf_agrupado_bytes,
                            file_name=file_name_agrupado,
                            mime="application/pdf",
                            use_container_width=True
                        )
                        log_event("export_pdf_grouped_fck", {
                            "rows": int(df_agrupado_base.shape[0]),
                            "fcks": list(map(str, df_agrupado_base.get("Fck Projeto", pd.Series(dtype=str)).dropna().unique().tolist())),
                            "file_name": file_name_agrupado,
                            "mode": report_mode,
                        })
                except Exception as e:
                    st.error(f"Falha ao gerar PDF agrupado por fck: {e}")

                # ============================================================
                # NOVO: Botão de PDF BÁSICO (Obra + 1ª tabela + Gráfico 1 + Verificação por CP + ID + rodapé)
                # ============================================================
                try:
                    pdf_basic_bytes = gerar_pdf(
                        df_view, stats_cp_idade,
                        fig1 if 'fig1' in locals() else None,
                        fig2 if 'fig2' in locals() else None,
                        fig3 if 'fig3' in locals() else None,
                        fig4 if 'fig4' in locals() else None,
                        str(df_view["Obra"].mode().iat[0]) if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty else "—",
                        (lambda _d: (
                            (min(_d).strftime('%d/%m/%Y') if min(_d) == max(_d) else f"{min(_d).strftime('%d/%m/%Y')} — {max(_d).strftime('%d/%m/%Y')}")
                            if _d else "—"
                        ))([_to_date_obj(x) for x in df_view["Data Certificado"].dropna().tolist()]),
                        _format_float_label(fck_active) if 'fck_active' in locals() and fck_active is not None else "—",
                        verif_fck_df2 if 'verif_fck_df2' in locals() else None,
                        cond_df if 'cond_df' in locals() else None,
                        pv_cp_status if 'pv_cp_status' in locals() else None,
                        s.get("rt_responsavel",""),
                        s.get("rt_cliente",""),
                        s.get("rt_cidade",""),
                        "__BASICO__",  # modo interno do relatório básico
                    )

                    file_name_basic = build_pdf_filename(df_view, uploaded_files)
                    if file_name_basic.lower().endswith(".pdf"):
                        file_name_basic = file_name_basic[:-4] + "_BASICO.pdf"
                    else:
                        file_name_basic = file_name_basic + "_BASICO.pdf"

                    st.download_button(
                        "📄 BAIXAR RELATÓRIO BASICO",
                        data=pdf_basic_bytes,
                        file_name=file_name_basic,
                        mime="application/pdf",
                        use_container_width=True
                    )
                    log_event("export_pdf_basic", {
                        "rows": int(df_view.shape[0]),
                        "relatorios": int(df_view["Relatório"].nunique()),
                        "obra": str(df_view["Obra"].mode().iat[0]) if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty else "—",
                        "file_name": file_name_basic,
                    })
                except Exception as e:
                    st.error(f"Falha ao gerar PDF Básico: {e}")

            except Exception as e:
                st.error(f"Falha ao gerar PDF: {e}")

        if has_df and CAN_EXPORT:
            try:
                stats_all_full = (df_view.groupby("Idade (dias)")["Resistência (MPa)"].agg(mean="mean", std="std", count="count").reset_index())
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
                    df_view.to_excel(writer, sheet_name="Individuais", index=False)
                    stats_cp_idade.to_excel(writer, sheet_name="Médias_DP", index=False)
                    comp_df = stats_all_full.rename(columns={"mean": "Média Real", "std": "DP Real", "count": "n"})
                    if 'est_df' in locals() and isinstance(est_df, pd.DataFrame) and (not est_df.empty):
                        comp_df = comp_df.merge(est_df.rename(columns={"Resistência (MPa)": "Estimado"}), on="Idade (dias)", how="outer").sort_values("Idade (dias)")
                        comp_df.to_excel(writer, sheet_name="Comparação", index=False)
                    else:
                        comp_df.to_excel(writer, sheet_name="Comparação", index=False)
                st.download_button("📊 Baixar Excel (XLSX)", data=excel_buffer.getvalue(),
                                   file_name="Relatorio_Graficos.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   use_container_width=True)
                log_event("export_excel", { "rows": int(df_view.shape[0]) })

                # ZIP com CSVs
                zip_buf = io.BytesIO()
                with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("Individuais.csv", df_view.to_csv(index=False, sep=";"))
                    z.writestr("Medias_DP.csv", stats_cp_idade.to_csv(index=False, sep=";"))
                    if 'est_df' in locals() and isinstance(est_df, pd.DataFrame) and (not est_df.empty):
                        z.writestr("Estimativas.csv", est_df.to_csv(index=False, sep=";"))
                    z.writestr("Comparacao.csv", comp_df.to_csv(index=False, sep=";"))
                st.download_button("🗃️ Baixar CSVs (ZIP)", data=zip_buf.getvalue(),
                                   file_name="Relatorio_Graficos_CSVs.zip",
                                   mime="application/zip", use_container_width=True)
                log_event("export_zip", { "rows": int(df_view.shape[0]) })

                # ZIP com gráficos (se existirem)
                try:
                    graph_zip = io.BytesIO()
                    with zipfile.ZipFile(graph_zip, "w", zipfile.ZIP_DEFLATED) as zg:
                        if 'fig1' in locals() and fig1 is not None:
                            buf = io.BytesIO(); fig1.savefig(buf, format="png", dpi=200, bbox_inches="tight")
                            zg.writestr("grafico1_real.png", buf.getvalue())
                        if 'fig2' in locals() and fig2 is not None:
                            buf = io.BytesIO(); fig2.savefig(buf, format="png", dpi=200, bbox_inches="tight")
                            zg.writestr("grafico2_estimado.png", buf.getvalue())
                        if 'fig3' in locals() and fig3 is not None:
                            buf = io.BytesIO(); fig3.savefig(buf, format="png", dpi=200, bbox_inches="tight")
                            zg.writestr("grafico3_comparacao.png", buf.getvalue())
                        if 'fig4' in locals() and fig4 is not None:
                            buf = io.BytesIO(); fig4.savefig(buf, format="png", dpi=200, bbox_inches="tight")
                            zg.writestr("grafico4_pareamento.png", buf.getvalue())
                    st.download_button("🖼️ Baixar gráficos (ZIP)", data=graph_zip.getvalue(),
                                       file_name="Graficos_relatorio.zip", mime="application/zip", use_container_width=True)
                except Exception:
                    pass

            except Exception:
                pass

        if st.button("📂 Ler Novo(s) Certificado(s)", use_container_width=True, key="btn_novo"):
            s["uploader_key"] += 1
            st.rerun()
else:
    st.info("Envie um PDF para visualizar os gráficos, relatório e exportações.")

st.markdown("---")
st.subheader("📘 Normas de Referência")
st.markdown("""
- **NBR 5738** – Concreto: Procedimento para moldagem e cura de corpos de prova
- **NBR 5739** – Concreto: Ensaio de compressão de corpos de prova cilíndricos
- **NBR 12655** – Concreto de cimento Portland: Preparo, controle e recebimento
- **NBR 7215** – Cimento Portland: Determinação da resistência à compressão
""")
st.markdown(
    """
    <div style="text-align:center; font-size:18px; font-weight:600; opacity:.9; margin-top:10px;">
      Sistema desenvolvido por IA e pela Habisolute Engenharia
    </div>
    """,
    unsafe_allow_html=True
)
