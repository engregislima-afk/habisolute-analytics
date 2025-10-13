# app.py — Habisolute Analytics (corrigido)
# Requisitos: streamlit, pandas, pdfplumber, matplotlib, reportlab, xlsxwriter

import io, re, json, base64, tempfile, zipfile, secrets, hashlib, os
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
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage, PageBreak
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas as pdfcanvas

# =============================================================================
# Rodapé / numeração do PDF
# =============================================================================
FOOTER_TEXT = (
    "Estes resultados referem-se exclusivamente às amostras ensaiadas; este documento poderá ser "
    "reproduzido somente na íntegra. Resultados sem considerar a incerteza da medição.  "
    "Sistema Desenvolvido pela Habisolute Engenharia."
)

class NumberedCanvas(pdfcanvas.Canvas):
    """Canvas que adiciona 'Página X de Y' e rodapé legal em todas as páginas."""
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
            self._draw_footer_and_pagenum(total_pages)
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

    def _draw_footer_and_pagenum(self, total_pages: int):
        w, h = self._pagesize
        text_font = "Helvetica"
        text_size = 7
        leading   = text_size + 1
        right_reserve = 95
        self.setFont(text_font, text_size)
        lines = self._wrap_footer(FOOTER_TEXT, text_font, text_size, w - 36 - right_reserve)
        base_y = 10
        for i, ln in enumerate(lines):
            y = base_y + i * leading
            if y > 28 - leading:
                break
            self.drawString(18, y, ln)
        self.setFont("Helvetica", 8)
        self.drawRightString(w - 18, 10, f"Página {self._pageNumber} de {total_pages}")

# =============================================================================
# Pastas e preferências
# =============================================================================
st.set_page_config(page_title="Habisolute — Relatórios", layout="wide")

PREFS_DIR = Path.home() / ".habisolute"
PREFS_DIR.mkdir(parents=True, exist_ok=True)
PREFS_PATH = PREFS_DIR / "prefs.json"
USERS_PATH = PREFS_DIR / "users.json"   # banco de usuários (JSON)

def _load_all_prefs() -> Dict[str, Any]:
    try:
        if PREFS_PATH.exists():
            return json.loads(PREFS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}

def _save_all_prefs(data: Dict[str, Any]) -> None:
    tmp = PREFS_DIR / "prefs.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PREFS_PATH)

def load_user_prefs(user_key: str = "default") -> Dict[str, Any]:
    return _load_all_prefs().get(user_key, {})

def save_user_prefs(prefs: Dict[str, Any], user_key: str = "default") -> None:
    data = _load_all_prefs()
    data[user_key] = prefs
    _save_all_prefs(data)

# =============================================================================
# Segurança / Cadastro
# =============================================================================
def _load_users() -> Dict[str, Any]:
    if USERS_PATH.exists():
        try:
            return json.loads(USERS_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
    return {}

def _save_users(db: Dict[str, Any]) -> None:
    tmp = PREFS_DIR / "users.tmp"
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USERS_PATH)

def _hash_pwd(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()

def register_user(username: str, password: str, full_name: str) -> Tuple[bool, str]:
    username = username.strip().lower()
    if not username or not password or not full_name.strip():
        return False, "Preencha nome, usuário e senha."
    db = _load_users()
    if username in db:
        return False, "Usuário já existe."
    salt = secrets.token_hex(16)
    db[username] = {
        "name": full_name.strip(),
        "salt": salt,
        "pwd": _hash_pwd(password, salt),
        "created_at": datetime.utcnow().isoformat() + "Z",
        "role": "user",
        "active": True
    }
    _save_users(db)
    return True, "Cadastro realizado com sucesso."

def authenticate(username: str, password: str) -> Tuple[bool, str, Dict[str, Any]]:
    db = _load_users()
    rec = db.get(username.strip().lower())
    if not rec or not rec.get("active", True):
        return False, "Usuário ou senha inválidos.", {}
    if _hash_pwd(password, rec["salt"]) != rec["pwd"]:
        return False, "Usuário ou senha inválidos.", {}
    return True, "OK", rec

# =============================================================================
# Estado
# =============================================================================
s = st.session_state
s.setdefault("logged_in", False)
s.setdefault("user", None)
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Claro corporativo"))
s.setdefault("brand", load_user_prefs().get("brand", "Laranja"))
s.setdefault("qr_url", load_user_prefs().get("qr_url", ""))
s.setdefault("uploader_key", 0)
s.setdefault("OUTLIER_SIGMA", 3.0)
s.setdefault("TOL_MP", 1.0)
s.setdefault("BATCH_MODE", False)
s.setdefault("_prev_batch", s["BATCH_MODE"])

# ler prefs por URL
def _apply_query_prefs():
    try:
        qp = st.query_params
        def _first(x): return x[0] if isinstance(x, list) else x
        theme = _first(qp.get("theme") or qp.get("t"))
        brand = _first(qp.get("brand") or qp.get("b"))
        qr    = _first(qp.get("q") or qp.get("qr") or qp.get("u"))
        if theme in ("Escuro moderno", "Claro corporativo"): s["theme_mode"] = theme
        if brand in ("Laranja", "Azul", "Verde", "Roxo"):    s["brand"]      = brand
        if qr: s["qr_url"] = qr
    except Exception:
        pass
_apply_query_prefs()

# =============================================================================
# Tema / CSS
# =============================================================================
BRAND_MAP = {
    "Laranja": ("#f97316", "#ea580c", "#c2410c"),
    "Azul":    ("#3b82f6", "#2563eb", "#1d4ed8"),
    "Verde":   ("#22c55e", "#16a34a", "#15803d"),
    "Roxo":    ("#a855f7", "#9333ea", "#7e22ce"),
}
brand, brand600, brand700 = BRAND_MAP.get(s["brand"], BRAND_MAP["Laranja"])

plt.rcParams.update({
    "font.size": 10, "axes.titlesize": 12, "axes.labelsize": 10,
    "axes.titleweight": "semibold", "figure.autolayout": False
})

if s["theme_mode"] == "Escuro moderno":
    plt.style.use("dark_background")
    css = f"""
    <style>
    :root {{ --brand:{brand}; --brand-600:{brand600}; --brand-700:{brand700};
      --bg:#0b0f19; --panel:#0f172a; --text:#e5e7eb; --muted:#a3a9b7; --line:rgba(148,163,184,.18);}}
    .stApp,.main{{background:var(--bg)!important;color:var(--text)!important}}
    .block-container{{padding-top:12px;max-width:1300px}}
    .h-card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 14px}}
    .h-kpi-label{{font-size:12px;color:var(--muted)}} .h-kpi{{font-size:22px;font-weight:800}}
    .pill{{display:inline-flex;align-items:center;gap:8px;padding:6px 10px;border-radius:999px;
           border:1px solid var(--line);background:rgba(148,163,184,.10);font-size:12.5px}}
    .brand-title{{font-weight:800;background:linear-gradient(90deg,var(--brand),var(--brand-700));
                 -webkit-background-clip:text;background-clip:text;color:transparent}}
    .login-card{{max-width:560px;margin:36px auto;background:var(--panel);border:1px solid var(--line);
                 border-radius:16px;padding:18px}}
    .login-title{{font-size:18px;font-weight:800;margin-bottom:8px}}
    </style>
    """
else:
    plt.style.use("default")
    css = f"""
    <style>
    :root {{ --brand:{brand}; --brand-600:{brand600}; --brand-700:{brand700};
      --bg:#f8fafc; --surface:#ffffff; --text:#0f172a; --muted:#64748b; --line:rgba(2,6,23,.08);}}
    .stApp,.main{{background:var(--bg)!important;color:var(--text)!important}}
    .block-container{{padding-top:12px;max-width:1300px}}
    .h-card{{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:12px 14px}}
    .h-kpi-label{{font-size:12px;color:var(--muted)}} .h-kpi{{font-size:22px;font-weight:800}}
    .pill{{display:inline-flex;align-items:center;gap:8px;padding:6px 10px;border-radius:999px;
           border:1px solid var(--line);background:#fff;font-size:12.5px}}
    .brand-title{{font-weight:800;background:linear-gradient(90deg,var(--brand),var(--brand-700));
                 -webkit-background-clip:text;background-clip:text;color:transparent}}
    .login-card{{max-width:560px;margin:36px auto;background:var(--surface);border:1px solid var(--line);
                 border-radius:16px;padding:18px}}
    .login-title{{font-size:18px;font-weight:800;margin-bottom:8px}}
    </style>
    """
st.markdown(css, unsafe_allow_html=True)

# toolbar botões
st.markdown(f"""
<style>
.h-toolbar{{display:grid;grid-template-columns:1fr;gap:10px;margin:6px 0 14px 0}}
@media (min-width:900px){{.h-toolbar{{grid-template-columns:1fr 1fr}}}}
.stButton>button,.stDownloadButton>button,.h-print-btn{{
  background:linear-gradient(180deg,{brand},{brand600})!important;color:#fff!important;border:0!important;
  border-radius:12px!important;padding:12px 16px!important;font-weight:800!important;
  box-shadow:0 8px 20px rgba(0,0,0,.08)!important;width:100%!important;
  transition:transform .06s ease, filter .1s ease;
}}
.stButton>button:hover,.stDownloadButton>button:hover,.h-print-btn:hover{{filter:brightness(1.06);transform:translateY(-1px)}}
.stButton>button:active,.stDownloadButton>button:active,.h-print-btn:active{{transform:translateY(0) scale(.99)}}
</style>
""", unsafe_allow_html=True)
# =============================================================================
# Login / Cadastro (dentro do sistema)
# =============================================================================
def show_auth():
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    tabs = st.tabs(["Entrar", "Cadastrar"])
    with tabs[0]:
        st.markdown("<div class='login-title'>🔐 Entrar — Habisolute Analytics</div>", unsafe_allow_html=True)
        with st.form("login_form", clear_on_submit=False):
            user = st.text_input("Usuário", key="login_user")
            pwd  = st.text_input("Senha", key="login_pass", type="password")
            ok = st.form_submit_button("Acessar")
        if ok:
            ok, msg, rec = authenticate(user, pwd)
            if ok:
                s["logged_in"] = True
                s["user"] = {"username": user.strip().lower(), **rec}
                st.rerun()
            else:
                st.error(msg)

    with tabs[1]:
        st.markdown("<div class='login-title'>👤 Cadastrar novo usuário</div>", unsafe_allow_html=True)
        with st.form("register_form", clear_on_submit=True):
            full = st.text_input("Nome completo")
            user = st.text_input("Usuário (login)")
            pwd1 = st.text_input("Senha", type="password")
            pwd2 = st.text_input("Confirmar senha", type="password")
            ok2 = st.form_submit_button("Criar conta")
        if ok2:
            if pwd1 != pwd2:
                st.error("As senhas não conferem.")
            else:
                ok, msg = register_user(user, pwd1, full)
                (st.success if ok else st.error)(msg)

    st.markdown("</div>", unsafe_allow_html=True)

if not s["logged_in"]:
    show_auth()
    st.stop()

# =============================================================================
# Barra de preferências
# =============================================================================
st.markdown("""
<style>.prefs-bar { margin-top: 28px; } @media (min-width:1100px){ .prefs-bar { margin-top: 36px; } }</style>
""", unsafe_allow_html=True)

with st.container():
    st.markdown("<div class='prefs-bar'>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([1.1, 1.1, 2.5, 1.1])

    with c1:
        s["theme_mode"] = st.radio(
            "Tema", ["Escuro moderno", "Claro corporativo"],
            index=0 if s.get("theme_mode") == "Escuro moderno" else 1, horizontal=True
        )
    with c2:
        s["brand"] = st.selectbox(
            "🎨 Cor da marca", ["Laranja", "Azul", "Verde", "Roxo"],
            index=["Laranja","Azul","Verde","Roxo"].index(s.get("brand","Laranja"))
        )
    with c3:
        s["qr_url"] = st.text_input("URL do resumo (QR opcional na capa do PDF)", value=s.get("qr_url",""),
                                    placeholder="https://exemplo.com/resumo")
    with c4:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("💾 Salvar como padrão", use_container_width=True, key="k_save"):
                save_user_prefs({"theme_mode": s["theme_mode"], "brand": s["brand"], "qr_url": s["qr_url"]})
                try:
                    qp = st.query_params
                    qp.update({"theme": s["theme_mode"], "brand": s["brand"], "q": s["qr_url"]})
                except Exception:
                    pass
                st.success("Preferências salvas.")
        with col_b:
            if st.button("Sair", use_container_width=True, key="k_logout"):
                s["logged_in"] = False
                s["user"] = None
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# Sidebar
# =============================================================================
with st.sidebar:
    st.markdown("### ⚙️ Opções do relatório")
    s["BATCH_MODE"] = st.toggle("Modo Lote (vários PDFs)", value=bool(s["BATCH_MODE"]))
    if s["BATCH_MODE"] != s["_prev_batch"]:
        s["_prev_batch"] = s["BATCH_MODE"]
        s["uploader_key"] += 1
    s["TOL_MP"] = st.slider("Tolerância Real × Estimado (MPa)", 0.0, 5.0, float(s["TOL_MP"]), 0.1)
    st.markdown("---")
    st.caption(f"Logado como: {s['user']['name'] if s.get('user') else '—'}")

TOL_MP = float(s["TOL_MP"])
BATCH_MODE = bool(s["BATCH_MODE"])

# =============================================================================
# Funções auxiliares de parsing (mesmas do seu app com pequenos ajustes)
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
            if t:  return t
    return None

def _parse_abatim_nf_pair(tok: str) -> Tuple[Optional[float], Optional[float]]:
    if not tok: return None, None
    t = str(tok).strip().lower().replace("±", "+-").replace("mm", "").replace(",", ".")
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
        m_nf = re.search(r"(?i)abat(?:imento|\.?im\.?)\s*(?:de\s*)?nf[^0-9]*"
                         r"(\d+(?:\.\d+)?)(?:\s*\+?-?\s*\d+(?:\.\d+)?)?\s*mm?", s_clean)
        if m_nf and abat_nf is None:
            try: abat_nf = float(m_nf.group(1))
            except Exception: pass
        m_obra = re.search(r"(?i)abat(?:imento|\.?im\.?).*(obra|medido em obra)[^0-9]*"
                           r"(\d+(?:\.\d+)?)\s*mm", s_clean)
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
    age_with_suffix = re.compile(r"^(\d{1,3})(?:\s*(?:dias?|d))\s*[:=]?", re.I)
    age_plain = re.compile(r"^(\d{1,3})\s*[:=]?", re.I)
    age_tokens = {3, 7, 14, 21, 28, 56, 63, 90}
    cut_keywords = ("mpa","abatimento","slump","nota","usina","relatório","relatorio","consumo","traço","traco","cimento","dosagem")
    for segment in parts:
        starts_immediate = bool(segment) and not segment[0].isspace()
        seg = segment.lstrip(" :=;-()[]")
        changed = True
        while changed:
            changed = False
            m = age_with_suffix.match(seg)
            if m and int(m.group(1)) in age_tokens:
                seg = seg[m.end():].lstrip(" :=;-()[]"); changed = True; continue
            if starts_immediate:
                m2 = age_plain.match(seg)
                if m2 and int(m2.group(1)) in age_tokens:
                    seg = seg[m2.end():].lstrip(" :=;-()[]"); changed = True; continue
        lower_seg = seg.lower(); cut_at = len(seg)
        for kw in cut_keywords:
            idx = lower_seg.find(kw)
            if idx != -1: cut_at = min(cut_at, idx)
        seg = seg[:cut_at]
        for num in re.findall(r"\d+(?:\.\d+)?", seg):
            try: val = float(num)
            except ValueError: continue
            if 3 <= val <= 120 and val not in values: values.append(val)
    return values

def _to_float_or_none(value: Any) -> Optional[float]:
    try: val = float(value)
    except (TypeError, ValueError): return None
    return None if pd.isna(val) else val

def _format_float_label(value: Optional[float]) -> str:
    if value is None or pd.isna(value): return "—"
    num = float(value); label = f"{num:.2f}".rstrip("0").rstrip(".")
    return label or f"{num:.2f}"

def _normalize_fck_label(value: Any) -> str:
    normalized = _to_float_or_none(value)
    if normalized is not None: return _format_float_label(normalized)
    raw = str(value).strip()
    if not raw or raw.lower() == "nan": return "—"
    return raw
# =============================================================================
# Parsing do certificado
# =============================================================================
def extrair_dados_certificado(uploaded_file):
    """
    Retorna DataFrame com colunas:
      Relatório, CP, Idade (dias), Resistência (MPa), Nota Fiscal, Local, Usina,
      Abatimento NF (mm), Abatimento NF tol (mm), Abatimento Obra (mm)
    + metadados: obra, data_relatorio, fck_projeto
    """
    try:
        raw = uploaded_file.read(); uploaded_file.seek(0)
    except Exception:
        raw = uploaded_file.getvalue()

    linhas_todas = []
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page in pdf.pages:
                txt = page.extract_text() or ""
                txt = re.sub(r"[“”]", "\"", txt)
                txt = re.sub(r"[’´`]", "'", txt)
                linhas_todas.extend([l.strip() for l in txt.split("\n") if l.strip()])
    except Exception:
        return (pd.DataFrame(columns=[
            "Relatório","CP","Idade (dias)","Resistência (MPa)","Nota Fiscal","Local",
            "Usina","Abatimento NF (mm)","Abatimento NF tol (mm)","Abatimento Obra (mm)"
        ]), "NÃO IDENTIFICADA", "NÃO IDENTIFICADA", "NÃO IDENTIFICADO")

    cp_regex = re.compile(r"^(?:[A-Z]{0,2})?\d{3,6}(?:\.\d{3})?$")
    data_regex = re.compile(r"\d{2}/\d{2}/\d{4}")
    data_token = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    tipo_token = re.compile(r"^A\d$", re.I)
    float_token = re.compile(r"^\d+[.,]\d+$")
    nf_regex = re.compile(r"^(?:\d{2,6}[.\-\/]?\d{3,6}|\d{5,12})$")
    pecas_regex = re.compile(r"(?i)peç[ac]s?\s+concretad[ao]s?:\s*(.*)")

    obra = "NÃO IDENTIFICADA"; data_relatorio = "NÃO IDENTIFICADA"; fck_projeto = "NÃO IDENTIFICADO"
    local_por_relatorio: Dict[str, str] = {}
    relatorio_atual = None
    fck_por_relatorio: Dict[str, List[float]] = {}
    fck_valores_globais: List[float] = []

    for sline in linhas_todas:
        if sline.startswith("Obra:"):
            obra = sline.replace("Obra:", "").strip().split(" Data")[0]
        m_data = data_regex.search(sline)
        if m_data and data_relatorio == "NÃO IDENTIFICADA": data_relatorio = m_data.group()
        if sline.startswith("Relatório:"):
            m_rel = re.search(r"Relatório:\s*(\d+)", sline)
            if m_rel: relatorio_atual = m_rel.group(1)
        m_pecas = pecas_regex.search(sline)
        if m_pecas and relatorio_atual: local_por_relatorio[relatorio_atual] = m_pecas.group(1).strip().rstrip(".")
        if "fck" in sline.lower():
            valores_fck = _extract_fck_values(sline)
            if valores_fck:
                if relatorio_atual: fck_por_relatorio.setdefault(relatorio_atual, []).extend(valores_fck)
                else: fck_valores_globais.extend(valores_fck)
                if not isinstance(fck_projeto, (int, float)):
                    try: fck_projeto = float(valores_fck[0])
                    except Exception: pass

    usina_nome = _limpa_usina_extra(_detecta_usina(linhas_todas))
    abat_nf_pdf, abat_obra_pdf = _detecta_abatimentos(linhas_todas)

    dados = []; relatorio_cabecalho = None
    for sline in linhas_todas:
        partes = sline.split()
        if sline.startswith("Relatório:"):
            m_rel = re.search(r"Relatório:\s*(\d+)", sline)
            if m_rel: relatorio_cabecalho = m_rel.group(1)
            continue

        if len(partes) >= 5 and cp_regex.match(partes[0]):
            try:
                cp = partes[0]; relatorio = relatorio_cabecalho or "NÃO IDENTIFICADO"
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
                resistencia, res_idx = None, None
                if idade_idx is not None:
                    for j in range(idade_idx + 1, len(partes)):
                        t = partes[j]
                        if float_token.match(t):
                            resistencia = float(t.replace(",", ".")); res_idx = j; break
                if idade is None or resistencia is None: continue

                nf, nf_idx = None, None
                start_nf = (res_idx + 1) if res_idx is not None else (idade_idx + 1)
                for j in range(start_nf, len(partes)):
                    tok = partes[j]
                    if nf_regex.match(tok) and tok != cp:
                        nf = tok; nf_idx = j; break

                abat_obra_val = None
                if i_data is not None:
                    for j in range(i_data - 1, max(-1, i_data - 6), -1):
                        tok = partes[j]
                        if re.fullmatch(r"\d{2,3}", tok):
                            v = int(tok)
                            if 20 <= v <= 250:
                                abat_obra_val = float(v); break

                abat_nf_val, abat_nf_tol = None, None
                if nf_idx is not None:
                    for tok in partes[nf_idx + 1: nf_idx + 5]:
                        v, tol = _parse_abatim_nf_pair(tok)
                        if v is not None and 20 <= v <= 250:
                            abat_nf_val = float(v); abat_nf_tol = float(tol) if tol is not None else None; break

                local = local_por_relatorio.get(relatorio)
                dados.append([
                    relatorio, cp, idade, resistencia, nf, local,
                    usina_nome,
                    (abat_nf_val if abat_nf_val is not None else abat_nf_pdf),
                    abat_nf_tol,
                    (abat_obra_val if abat_obra_val is not None else abat_obra_pdf)
                ])
            except Exception:
                pass

    df = pd.DataFrame(dados, columns=[
        "Relatório", "CP", "Idade (dias)", "Resistência (MPa)", "Nota Fiscal", "Local",
        "Usina", "Abatimento NF (mm)", "Abatimento NF tol (mm)", "Abatimento Obra (mm)"
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
                try: fallback_fck = float(cand); break
                except Exception: continue
            if fallback_fck is not None: fck_projeto = fallback_fck

        if rel_map or fallback_fck is not None:
            df["Relatório"] = df["Relatório"].astype(str)
            df["Fck Projeto"] = df["Relatório"].map(rel_map)
            if fallback_fck is not None:
                df["Fck Projeto"] = df["Fck Projeto"].fillna(fallback_fck)

    return df, obra, data_relatorio, fck_projeto

# =============================================================================
# KPIs / gráficos helpers
# =============================================================================
def compute_exec_kpis(df_view: pd.DataFrame, fck_val: Optional[float]):
    def _pct_hit(age):
        if fck_val is None or pd.isna(fck_val): return None
        g = df_view[df_view["Idade (dias)"] == age].groupby("CP")["Resistência (MPa)"].mean()
        if g.empty: return None
        return float((g >= fck_val).mean() * 100.0)

    pct28 = _pct_hit(28); pct63 = _pct_hit(63)
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
    return {"pct28": pct28, "pct63": pct63, "media": media_geral, "dp": dp_geral,
            "n_rel": n_rel, "status_txt": status_txt, "status_cor": status_cor}

def place_right_legend(ax):
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), loc="upper left",
              bbox_to_anchor=(1.02, 1.0), frameon=False, ncol=1,
              handlelength=2.2, handletextpad=0.8, labelspacing=0.35, prop={"size": 9})
    plt.subplots_adjust(right=0.80)

def _img_from_fig(_fig, w=540, h=340):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    _fig.savefig(tmp.name, dpi=200, bbox_inches="tight")
    return RLImage(tmp.name, width=w, height=h)

# =============================================================================
# PDF (única definição de gerar_pdf)
# =============================================================================
def gerar_pdf(
    df: pd.DataFrame,
    stats: pd.DataFrame,
    fig1, fig2, fig3, fig4,
    obra_label: str, data_label: str, fck_label: str,
    verif_fck_df: Optional[pd.DataFrame],
    cond_df: Optional[pd.DataFrame],
    pareamento_df: Optional[pd.DataFrame],
    pv_detalhe: Optional[pd.DataFrame],
) -> bytes:
    """Gera o relatório em PDF completo (cabeçalho, gráficos maiores, verificações e rodapé)."""

    def _abat_nf_label(df_: pd.DataFrame) -> str:
        snf = pd.to_numeric(df_.get("Abatimento NF (mm)"), errors="coerce").dropna()
        stol = pd.to_numeric(df_.get("Abatimento NF tol (mm)"), errors="coerce").dropna()
        if snf.empty: return "—"
        v = float(snf.mode().iloc[0]); t = float(stol.mode().iloc[0]) if not stol.empty else 0.0
        return f"{v:.0f} ± {t:.0f} mm"

    use_landscape = (len(df.columns) >= 8)
    pagesize = landscape(A4) if use_landscape else A4

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=pagesize,
                            leftMargin=18, rightMargin=18, topMargin=34, bottomMargin=54)

    styles = getSampleStyleSheet()
    styles["Title"].fontName="Helvetica-Bold";  styles["Title"].fontSize=18
    styles["Heading2"].fontName="Helvetica-Bold"; styles["Heading2"].fontSize=14
    styles["Heading3"].fontName="Helvetica-Bold"; styles["Heading3"].fontSize=12
    styles["Normal"].fontName="Helvetica"; styles["Normal"].fontSize=9

    story = []
    # Cabeçalho
    story.append(Paragraph("<b>Habisolute Engenharia e Controle Tecnológico</b>", styles['Title']))
    story.append(Paragraph("Relatório de Rompimento de Corpos de Prova", styles['Heading2']))
    if s.get("qr_url"):
        story.append(Paragraph(f"<b>Resumo/QR:</b> {s['qr_url']}", styles['Normal']))
    story.append(Paragraph(f"<b>Obra:</b> {obra_label}", styles['Normal']))
    story.append(Paragraph(f"<b>Data do relatório:</b> {data_label}", styles['Normal']))
    story.append(Paragraph(f"<b>fck de projeto:</b> {fck_label} MPa", styles['Normal']))
    story.append(Paragraph(f"<b>Abatimento de NF:</b> {_abat_nf_label(df)}", styles['Normal']))
    story.append(Spacer(1, 8))

    # Tabela principal
    headers = ["Relatório","CP","Idade (dias)","Resistência (MPa)","Nota Fiscal","Local","Usina","Abatimento NF (mm)","Abatimento Obra (mm)"]
    rows = df[headers].values.tolist()
    table = Table([headers] + rows, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
        ("GRID",(0,0),(-1,-1),0.5,colors.black),
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
        ("FONTSIZE",(0,0),(-1,-1),8.6),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),3),("RIGHTPADDING",(0,0),(-1,-1),3),
        ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2),
    ]))
    story.append(table); story.append(Spacer(1, 10))

    # Resumo estatístico
    if isinstance(stats, pd.DataFrame) and not stats.empty:
        story.append(Paragraph("Resumo Estatístico (Média + DP)", styles['Heading3']))
        stt = [["CP","Idade (dias)","Média","DP","n"]] + stats.values.tolist()
        t2 = Table(stt, repeatRows=1)
        t2.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
            ("GRID",(0,0),(-1,-1),0.5,colors.black),
            ("ALIGN",(0,0),(-1,-1),"CENTER"),
            ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
            ("FONTSIZE",(0,0),(-1,-1),8.6),
        ]))
        story.append(t2); story.append(Spacer(1, 10))

    # Gráficos (maiores)
    if fig1: story.append(_img_from_fig(fig1, 540, 340)); story.append(Spacer(1, 8))
    if fig2: story.append(_img_from_fig(fig2, 540, 340)); story.append(Spacer(1, 8))
    if fig3: story.append(_img_from_fig(fig3, 540, 340)); story.append(Spacer(1, 8))
    if fig4: story.append(_img_from_fig(fig4, 540, 340)); story.append(Spacer(1, 8))

    # Verificação do fck
    if isinstance(verif_fck_df, pd.DataFrame) and not verif_fck_df.empty:
        story.append(PageBreak())
        story.append(Paragraph("Verificação do fck de Projeto (média por idade)", styles["Heading3"]))
        rows_v = [["Idade (dias)","Média Real (MPa)","fck Projeto (MPa)","Status"]]
        for _, r in verif_fck_df.iterrows():
            rows_v.append([
                r["Idade (dias)"],
                f"{r['Média Real (MPa)']:.3f}" if pd.notna(r['Média Real (MPa)']) else "—",
                f"{r.get('fck Projeto (MPa)', float('nan')):.3f}" if pd.notna(r.get('fck Projeto (MPa)', float('nan'))) else "—",
                r["Status"]
            ])
        tv = Table(rows_v, repeatRows=1)
        tv.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
            ("GRID",(0,0),(-1,-1),0.5,colors.black),
            ("ALIGN",(0,0),(-2,-1),"CENTER"),
            ("ALIGN",(-1,1),(-1,-1),"LEFT"),
            ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
            ("FONTSIZE",(0,0),(-1,-1),8.6),
        ]))
        story.append(tv); story.append(Spacer(1, 10))

    # Condição Real × Estimado
    if isinstance(cond_df, pd.DataFrame) and not cond_df.empty:
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
        tc.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
            ("GRID",(0,0),(-1,-1),0.5,colors.black),
            ("ALIGN",(0,0),(-2,-1),"CENTER"),
            ("ALIGN",(-1,1),(-1,-1),"LEFT"),
            ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
            ("FONTSIZE",(0,0),(-1,-1),8.6),
        ]))
        story.append(tc); story.append(Spacer(1, 10))

    # Pareamento ponto a ponto
    if isinstance(pareamento_df, pd.DataFrame) and not pareamento_df.empty:
        story.append(Paragraph("Pareamento ponto-a-ponto (Real × Estimado)", styles["Heading3"]))
        head_p = ["CP","Idade (dias)","Real (MPa)","Estimado (MPa)","Δ","Status"]
        rows_p = pareamento_df[head_p].values.tolist()
        tp = Table([head_p] + rows_p, repeatRows=1)
        tp.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
            ("GRID",(0,0),(-1,-1),0.5,colors.black),
            ("ALIGN",(0,0),(-1,-1),"CENTER"),
            ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
            ("FONTSIZE",(0,0),(-1,-1),8.3),
        ]))
        story.append(tp); story.append(Spacer(1, 10))

    # Verificação detalhada por CP (pivot completo)
    if isinstance(pv_detalhe, pd.DataFrame) and not pv_detalhe.empty:
        story.append(PageBreak())
        story.append(Paragraph("Verificação detalhada por CP (7/28/63 dias)", styles["Heading3"]))
        head_v = list(pv_detalhe.columns)
        rows_v2 = pv_detalhe.values.tolist()
        tv2 = Table([head_v] + rows_v2, repeatRows=1)
        tv2.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
            ("GRID",(0,0),(-1,-1),0.5,colors.black),
            ("ALIGN",(0,0),(-1,-1),"CENTER"),
            ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
            ("FONTSIZE",(0,0),(-1,-1),8.1),
        ]))
        story.append(tv2)

    doc.build(story, canvasmaker=NumberedCanvas)
    pdf = buffer.getvalue(); buffer.close()
    return pdf
# ============================== PARTE 4 — Relatórios, Verificações Detalhadas, Exportações e Rodapé ==============================

# ===== Verificação detalhada por CP (7/28/63 dias) =====
st.markdown("#### ✅ Verificação detalhada por CP (7/28/63 dias)")

if ("df_view" not in locals()) or (not isinstance(df_view, pd.DataFrame)) or df_view.empty:
    st.info("Envie um PDF para visualizar a verificação detalhada.")
    pv = pd.DataFrame()
else:
    if ("Idade (dias)" not in df_view.columns) or ("Resistência (MPa)" not in df_view.columns):
        st.info("Sem colunas necessárias para a verificação (Idade/Resistência).")
        pv = pd.DataFrame()
    else:
        tmp_v = df_view[df_view["Idade (dias)"].isin([7, 28, 63])].copy()
        if tmp_v.empty:
            st.info("Sem CPs de 7/28/63 dias no filtro atual.")
            pv = pd.DataFrame()
        else:
            tmp_v["MPa"] = pd.to_numeric(tmp_v["Resistência (MPa)"], errors="coerce")
            # enumera réplicas por CP+Idade
            tmp_v["rep"] = tmp_v.groupby(["CP", "Idade (dias)"]).cumcount() + 1

            # Pivot mantendo réplicas
            pv_multi = tmp_v.pivot_table(
                index="CP",
                columns=["Idade (dias)", "rep"],
                values="MPa",
                aggfunc="first"
            ).sort_index(axis=1)

            # Garante níveis 7/28/63
            for age in [7, 28, 63]:
                if age not in pv_multi.columns.get_level_values(0):
                    pv_multi[(age, 1)] = pd.NA

            # Reordena colunas
            ordered = []
            for age in [7, 28, 63]:
                reps = sorted([r for (a, r) in pv_multi.columns if a == age])
                for r in reps:
                    ordered.append((age, r))
            pv_multi = pv_multi.reindex(columns=ordered)

            # Cabeçalhos achatados
            def _flat(age, rep):
                base = f"{age}d"
                return f"{base} (MPa)" if rep == 1 else f"{base} #{rep} (MPa)"

            pv = pv_multi.copy()
            pv.columns = [_flat(a, r) for (a, r) in pv_multi.columns]
            pv = pv.reset_index()

            # Ordenação de CP numericamente (quando possível)
            try:
                pv["__cp_sort__"] = pv["CP"].astype(str).str.extract(r"(\d+)").astype(float)
            except Exception:
                pv["__cp_sort__"] = range(len(pv))
            pv = pv.sort_values(["__cp_sort__", "CP"]).drop(columns="__cp_sort__", errors="ignore")

            # fck ativo (do conjunto filtrado)
            fck_series_focus2 = pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
            fck_active2 = float(fck_series_focus2.mode().iloc[0]) if not fck_series_focus2.empty else None

            # Médias por idade (no índice de pv_multi)
            media_7  = pv_multi[7].mean(axis=1)  if 7  in pv_multi.columns.get_level_values(0) else pd.Series(pd.NA, index=pv_multi.index)
            media_63 = pv_multi[63].mean(axis=1) if 63 in pv_multi.columns.get_level_values(0) else pd.Series(pd.NA, index=pv_multi.index)

            # 28d: todas as réplicas do CP devem ser ≥ fck para "Atingiu"
            if 28 in pv_multi.columns.get_level_values(0) and (fck_active2 is not None) and not pd.isna(fck_active2):
                cols28 = pv_multi[28]
                def _all_reps_ok(row):
                    vals = row.dropna().astype(float)
                    if vals.empty:
                        return None
                    return bool((vals >= float(fck_active2)).all())
                ok28 = cols28.apply(_all_reps_ok, axis=1)
            else:
                ok28 = pd.Series([None] * pv_multi.shape[0], index=pv_multi.index)

            def _status_text_media(media_idade, age, fckp):
                if pd.isna(media_idade) or (fckp is None) or pd.isna(fckp):
                    return "⚪ Sem dados"
                if age == 7:
                    return "🟡 Informativo (7d)"
                return "🟢 Atingiu fck" if float(media_idade) >= float(fckp) else "🔴 Não atingiu fck"

            def _status_from_ok(ok):
                if ok is None:
                    return "⚪ Sem dados"
                return "🟢 Atingiu fck" if ok else "🔴 Não atingiu fck"

            status_df = pd.DataFrame({
                "Status 7d":  [ _status_text_media(v, 7,  fck_active2) for v in media_7.reindex(pv_multi.index) ],
                "Status 28d": [ _status_from_ok(v) for v in ok28.reindex(pv_multi.index) ],
                "Status 63d": [ _status_text_media(v, 63, fck_active2) for v in media_63.reindex(pv_multi.index) ],
            }, index=pv_multi.index)

            pv = pv.merge(status_df, left_on="CP", right_index=True, how="left")

            # Organização por idade com status ao lado de cada grupo
            cols_cp = ["CP"]
            cols_7   = [c for c in pv.columns if c.startswith("7d")]
            cols_28  = [c for c in pv.columns if c.startswith("28d")]
            cols_63  = [c for c in pv.columns if c.startswith("63d")]

            ordered_cols = (
                cols_cp
                + cols_7  + (["Status 7d"]  if "Status 7d"  in pv.columns else [])
                + cols_28 + (["Status 28d"] if "Status 28d" in pv.columns else [])
                + cols_63 + (["Status 63d"] if "Status 63d" in pv.columns else [])
            )
            ordered_cols = [c for c in ordered_cols if c in pv.columns]

            pv = pv.rename(columns={
                "Status 7d": "7 dias — Status",
                "Status 28d": "28 dias — Status",
                "Status 63d": "63 dias — Status",
            })
            ordered_cols = [
                "7 dias — Status" if c == "Status 7d" else
                "28 dias — Status" if c == "Status 28d" else
                "63 dias — Status" if c == "Status 63d" else c
                for c in ordered_cols
            ]
            pv = pv[ordered_cols]

            st.dataframe(pv, use_container_width=True)

# ===== PDF / Impressão / Exportações =====
has_df = ("df_view" in locals()) and isinstance(df_view, pd.DataFrame) and (not df_view.empty)

if has_df:
    # Labels defensivos (caso usuário troque os filtros)
    try:
        obra_label_pdf = str(df_view["Obra"].mode().iat[0]) if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty else "—"
    except Exception:
        obra_label_pdf = "—"
    try:
        data_label_pdf = str(df_view["Data Certificado"].mode().iat[0]) if "Data Certificado" in df_view.columns and not df_view["Data Certificado"].dropna().empty else "—"
    except Exception:
        data_label_pdf = "—"

    # fck ativo já foi calculado durante os gráficos/KPIs; recalcula defensivamente:
    _fck_series_all = pd.to_numeric(df_view.get("Fck Projeto"), errors="coerce").dropna()
    fck_active = float(_fck_series_all.mode().iloc[0]) if not _fck_series_all.empty else None
    fck_label_pdf = _format_float_label(fck_active)

    # tenta capturar variáveis possivelmente definidas nas seções de gráficos
    try:
        _verif_fck_df = verif_fck_df
    except NameError:
        _verif_fck_df = pd.DataFrame()

    try:
        _cond_df = cond_df
    except NameError:
        _cond_df = pd.DataFrame()

    try:
        _pareamento_df = pareamento_df
    except NameError:
        _pareamento_df = pd.DataFrame()

    # Tabelas estatísticas por CP×Idade (já calculadas antes); recalcula se necessário
    try:
        _stats_cp_idade = stats_cp_idade
    except NameError:
        _stats_cp_idade = (
            df_view.groupby(["CP", "Idade (dias)"])["Resistência (MPa)"]
                   .agg(Média="mean", Desvio_Padrão="std", n="count")
                   .reset_index()
        )

    # Figuras (seções anteriores); se não existirem, usa None
    fig1 = locals().get("fig1")
    fig2 = locals().get("fig2")
    fig3 = locals().get("fig3")
    fig4 = locals().get("fig4")

    # Geração do PDF com a verificação detalhada (pv) incluída
    try:
        pdf_bytes = gerar_pdf(
            df_view,
            _stats_cp_idade,
            fig1, fig2, fig3, fig4,
            obra_label_pdf, data_label_pdf, fck_label_pdf,
            _verif_fck_df if isinstance(_verif_fck_df, pd.DataFrame) else pd.DataFrame(),
            _cond_df if isinstance(_cond_df, pd.DataFrame) else pd.DataFrame(),
            _pareamento_df if isinstance(_pareamento_df, pd.DataFrame) else pd.DataFrame(),
            pv if isinstance(pv, pd.DataFrame) else pd.DataFrame(),   # << pv_detalhe
        )
        _nome_pdf = "Relatorio_Habisolute.pdf"
        st.download_button("📄 Baixar Relatório (PDF)", data=pdf_bytes,
                           file_name=_nome_pdf, mime="application/pdf",
                           use_container_width=True)
    except Exception as e:
        st.error(f"Falha ao gerar o PDF: {e}")

    # Bloco de impressão (HTML) — usa o PDF completo
    if "render_print_block" in globals() and "pdf_bytes" in locals() and pdf_bytes:
        try:
            render_print_block(
                pdf_bytes, None,
                locals().get("brand", "#3b82f6"),
                locals().get("brand600", "#2563eb")
            )
        except Exception:
            pass

    # ===== Exportação: Excel (XLSX) e CSV (ZIP) =====
    try:
        stats_all_full = (
            df_view.groupby("Idade (dias)")["Resistência (MPa)"]
                  .agg(mean="mean", std="std", count="count")
                  .reset_index()
        )

        # Tenta recuperar est_df (curva estimada) se existir
        _est_df = None
        try:
            if 'est_df' in locals() and isinstance(est_df, pd.DataFrame) and not est_df.empty:
                _est_df = est_df.copy()
        except NameError:
            _est_df = None

        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
            # Aba 1 — Individuais
            df_view.to_excel(writer, sheet_name="Individuais", index=False)

            # Aba 2 — Médias e DP por CP×Idade
            _stats_cp_idade.to_excel(writer, sheet_name="Médias_DP", index=False)

            # Aba 3 — Comparação (Real x Estimado), se houver est_df
            comp_df = stats_all_full.rename(
                columns={"mean": "Média Real", "std": "DP Real", "count": "n"}
            )
            if isinstance(_est_df, pd.DataFrame) and not _est_df.empty:
                comp_df = comp_df.merge(
                    _est_df.rename(columns={"Resistência (MPa)": "Estimado"}),
                    on="Idade (dias)", how="outer"
                ).sort_values("Idade (dias)")
                comp_df.to_excel(writer, sheet_name="Comparação", index=False)
            else:
                comp_df.to_excel(writer, sheet_name="Comparação", index=False)

            # Aba 4 — Verificação Detalhada por CP (pivot)
            if isinstance(pv, pd.DataFrame) and not pv.empty:
                pv.to_excel(writer, sheet_name="Verificação_Detalhada_CP", index=False)

            # Inserção de imagens (opcional)
            try:
                ws_md = writer.sheets.get("Médias_DP")
                if ws_md is not None and fig1 is not None:
                    img1 = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                    fig1.savefig(img1.name, dpi=150, bbox_inches="tight")
                    ws_md.insert_image("H2", img1.name, {"x_scale": 0.8, "y_scale": 0.8})
            except Exception:
                pass

            try:
                ws_comp = writer.sheets.get("Comparação")
                if ws_comp is not None and fig2 is not None:
                    img2 = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                    fig2.savefig(img2.name, dpi=150, bbox_inches="tight")
                    ws_comp.insert_image("H20", img2.name, {"x_scale": 0.8, "y_scale": 0.8})
                if ws_comp is not None and fig3 is not None:
                    img3 = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                    fig3.savefig(img3.name, dpi=150, bbox_inches="tight")
                    ws_comp.insert_image("H38", img3.name, {"x_scale": 0.8, "y_scale": 0.8})
            except Exception:
                pass

        st.download_button(
            "📊 Baixar Excel (XLSX)",
            data=excel_buffer.getvalue(),
            file_name="Relatorio_Habisolute.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("Individuais.csv", df_view.to_csv(index=False, sep=";"))
            z.writestr("Medias_DP.csv", _stats_cp_idade.to_csv(index=False, sep=";"))
            if isinstance(_est_df, pd.DataFrame) and not _est_df.empty:
                z.writestr("Estimativas.csv", _est_df.to_csv(index=False, sep=";"))
            # Inclui comparação se existir
            try:
                z.writestr("Comparacao.csv", comp_df.to_csv(index=False, sep=";"))
            except Exception:
                pass
            # Inclui verificação detalhada por CP
            if isinstance(pv, pd.DataFrame) and not pv.empty:
                z.writestr("Verificacao_Detalhada_CP.csv", pv.to_csv(index=False, sep=";"))

        st.download_button(
            "🗃️ Baixar CSVs (ZIP)",
            data=zip_buf.getvalue(),
            file_name="Relatorio_Habisolute_CSVs.zip",
            mime="application/zip",
            use_container_width=True
        )
    except Exception as e:
        st.error(f"Falha ao exportar planilhas/CSVs: {e}")
else:
    st.info("Envie um PDF para visualizar os gráficos, relatório e exportações.")

# Botão para reiniciar leitura
if st.button("📂 Ler Novo(s) Certificado(s)", use_container_width=True, key="btn_novo"):
    s["uploader_key"] += 1
    st.rerun()

# (opcional) separador antes do rodapé
st.markdown("---")

# ===== Rodapé: Normas =====
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
      Sistema desenvolvido pela Habisolute Engenharia
    </div>
    """,
    unsafe_allow_html=True
)
# =======================================================================================================================

