# app.py — Habisolute Analytics (login + painel + tema + capa QR + conflitos NF/CP + PDF/Excel)
# =============================================================================================

from __future__ import annotations

import io, re, json, base64, tempfile, zipfile, hashlib, warnings
from datetime import datetime, date
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
    Image as RLImage, PageBreak, KeepInFrame
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.graphics.barcode import qr as rl_qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import mm

# ===== Rodapé e numeração do PDF =====
FOOTER_TEXT = (
    "Estes resultados referem-se exclusivamente às amostras ensaiadas. "
    "Este documento poderá ser reproduzido somente na íntegra. "
    "Resultados apresentados sem considerar a incerteza de medição +- 0,90Mpa."
)
FOOTER_BRAND_TEXT = "Sistema Desenvolvido pela Habisolute Engenharia"

class NumberedCanvas(pdfcanvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs); self._saved_page_states = []
    def showPage(self): self._saved_page_states.append(dict(self.__dict__)); self._startPage()
    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer_and_pagenum(total_pages)
            super().showPage()
        super().save()
    def _wrap_footer(self, text, font_name="Helvetica", font_size=7, max_width=None):
        if max_width is None: max_width = self._pagesize[0] - 36 - 120
        words = text.split(); lines, line = [], ""
        for w in words:
            test = (line + " " + w).strip()
            if self.stringWidth(test, font_name, font_size) <= max_width: line = test
            else:
                if line: lines.append(line)
                line = w
        if line: lines.append(line)
        return lines
    def _draw_footer_and_pagenum(self, total_pages: int):
        w, h = self._pagesize
        self.setFont("Helvetica", 7)
        for i, ln in enumerate(self._wrap_footer(FOOTER_TEXT, "Helvetica", 7, w - 36 - 100)):
            y = 10 + i * 8
            if y > 20: break
            self.drawString(18, y, ln)
        self.setFont("Helvetica-Oblique", 8); self.drawCentredString(w/2.0, 26, FOOTER_BRAND_TEXT)
        self.setFont("Helvetica", 8); self.drawRightString(w - 18, 10, f"Página {self._pageNumber} de {total_pages}")

# =============================================================================
# Configuração básica
# =============================================================================
st.set_page_config(page_title="Habisolute — Relatórios", layout="wide")

PREFS_DIR = Path.home() / ".habisolute"; PREFS_DIR.mkdir(parents=True, exist_ok=True)
PREFS_PATH = PREFS_DIR / "prefs.json"; USERS_DB = PREFS_DIR / "users.json"

# ----- prefs util -----
def _save_all_prefs(data: Dict[str, Any]) -> None:
    tmp = PREFS_DIR / "prefs.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(PREFS_PATH)
def _load_all_prefs() -> Dict[str, Any]:
    try:
        if PREFS_PATH.exists(): return json.loads(PREFS_PATH.read_text(encoding="utf-8")) or {}
    except Exception: pass
    return {}
def load_user_prefs(key: str = "default") -> Dict[str, Any]: return _load_all_prefs().get(key, {})
def save_user_prefs(prefs: Dict[str, Any], key: str = "default") -> None:
    data = _load_all_prefs(); data[key] = prefs; _save_all_prefs(data)

# ===== Estado =====
s = st.session_state
s.setdefault("logged_in", False); s.setdefault("username", None); s.setdefault("is_admin", False)
s.setdefault("must_change", False)
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Claro corporativo"))
s.setdefault("brand", load_user_prefs().get("brand", "Laranja"))
s.setdefault("qr_url", load_user_prefs().get("qr_url", ""))
s.setdefault("uploader_key", 0); s.setdefault("OUTLIER_SIGMA", 3.0)
s.setdefault("TOL_MP", 1.0); s.setdefault("BATCH_MODE", False); s.setdefault("_prev_batch", s["BATCH_MODE"])
s.setdefault("ALLOW_PDF_WITH_CONFLICTS", False)

# Recupera usuário após refresh se necessário
if s.get("logged_in") and not s.get("username"):
    _p = load_user_prefs()
    if _p.get("last_user"): s["username"] = _p["last_user"]

# --- preferências via URL ---
def _apply_query_prefs():
    try:
        qp = st.query_params
        def _first(x):
            if x is None: return None
            return x[0] if isinstance(x, list) else x
        theme = _first(qp.get("theme") or qp.get("t"))
        brand = _first(qp.get("brand") or qp.get("b"))
        qr    = _first(qp.get("q") or qp.get("qr") or qp.get("u"))
        if theme in ("Escuro moderno","Claro corporativo"): s["theme_mode"] = theme
        if brand in ("Laranja","Azul","Verde","Roxo"): s["brand"] = brand
        if qr: s["qr_url"] = qr
    except Exception: pass
_apply_query_prefs()

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

plt.rcParams.update({"font.size":10,"axes.titlesize":12,"axes.labelsize":10,"axes.titleweight":"semibold","figure.autolayout":False})

if s["theme_mode"] == "Escuro moderno":
    plt.style.use("dark_background")
    css = f"""
    <style>
    :root {{
      --brand:{brand}; --brand-600:{brand600}; --brand-700:{brand700};
      --bg:#0b0f19; --panel:#0f172a; --surface:#111827; --text:#e5e7eb; --muted:#a3a9b7; --line:rgba(148,163,184,.18);
    }}
    .stApp, .main {{ background: var(--bg) !important; color: var(--text) !important; }}
    .block-container{{ padding-top: 56px; max-width: 1300px; }}
    .h-card{{ background: var(--panel); border:1px solid var(--line); border-radius:14px; padding:12px 14px; }}
    .h-kpi-label{{ font-size:12px; color:var(--muted) }} .h-kpi{{ font-size:22px; font-weight:800; }}
    .pill{{ display:inline-flex; gap:8px; padding:6px 10px; border-radius:999px; border:1px solid var(--line); background:rgba(148,163,184,.10); font-size:12.5px; }}
    .stButton > button, .stDownloadButton > button {{
      background: linear-gradient(180deg, {brand}, {brand600}) !important; color:#fff !important; border:0 !important; border-radius:12px !important;
      padding:12px 16px !important; font-weight:800 !important; box-shadow:0 8px 20px rgba(0,0,0,.18) !important;
    }}
    .stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] > div, .stMultiSelect div[data-baseweb="select"] > div, .stDateInput input {{
      background: var(--surface) !important; color: var(--text) !important; border-color: var(--line) !important;
    }}
    .stExpander > details > summary {{ background: var(--panel) !important; color: var(--text) !important; border:1px solid var(--line); border-radius:10px; padding:8px 12px; }}
    </style>
    """
else:
    plt.style.use("default")
    css = f"""
    <style>
    :root {{
      --brand:{brand}; --brand-600:{brand600}; --brand-700:{brand700};
      --bg:#f8fafc; --surface:#ffffff; --panel:#ffffff; --text:#0f172a; --muted:#475569; --line:rgba(2,6,23,.10);
    }}
    .stApp, .main {{ background: var(--bg) !important; color: var(--text) !important; }}
    .block-container{{ padding-top: 56px; max-width: 1300px; }}
    .h-card{{ background: var(--panel); border:1px solid var(--line); border-radius:14px; padding:12px 14px; }}
    .h-kpi-label{{ font-size:12px; color:var(--muted) }} .h-kpi{{ font-size:22px; font-weight:800; }}
    .pill{{ display:inline-flex; gap:8px; padding:6px 10px; border-radius:999px; border:1px solid var(--line); background:#fff; color:var(--text); font-size:12.5px; }}
    .stButton > button, .stDownloadButton > button {{
      background: linear-gradient(180deg, {brand}, {brand600}) !important; color:#fff !important; border:0 !important; border-radius:12px !important;
      padding:12px 16px !important; font-weight:800 !important; box-shadow:0 8px 20px rgba(0,0,0,.08) !important;
    }}
    .stTextInput input, .stNumberInput input, .stDateInput input {{ background:#fff !important; color:var(--text) !important; border:1px solid var(--line) !important; }}
    .stSelectbox div[data-baseweb="select"] > div, .stMultiSelect div[data-baseweb="select"] > div {{ background:#fff !important; color:var(--text) !important; border:1px solid var(--line) !important; }}
    .stExpander > details > summary {{ background:#fff !important; color:var(--text) !important; border:1px solid var(--line); border-radius:10px; padding:8px 12px; }}
    </style>
    """
st.markdown(css, unsafe_allow_html=True)

# -------- Cabeçalho (será mostrado só depois do login) ----------
def _render_header():
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown("<div class='app-header'><span class='brand-title' style='font-weight:800; font-size:22px; color: var(--text)'>🏗️ Habisolute IA</span></div>", unsafe_allow_html=True)
    st.caption("Envie certificados em PDF e gere análises, gráficos, KPIs e relatório final com capa personalizada.")

# =============================================================================
# Autenticação & gerenciamento de usuários
# =============================================================================
def _hash_password(pw: str) -> str: return hashlib.sha256(("habisolute|" + pw).encode("utf-8")).hexdigest()
def _verify_password(pw: str, hashed: str) -> bool:
    try: return _hash_password(pw) == hashed
    except Exception: return False

def _save_users(data: Dict[str, Any]) -> None:
    tmp = USERS_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(USERS_DB)
def _load_users() -> Dict[str, Any]:
    def _bootstrap_admin(db: Dict[str, Any]) -> Dict[str, Any]:
        db.setdefault("users", {})
        if "admin" not in db["users"]:
            db["users"]["admin"] = {
                "password": _hash_password("1234"), "is_admin": True, "active": True, "must_change": True,
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
                            users_map[uname] = {"password": _hash_password("1234"), "is_admin": (uname=="admin"),
                                                "active": True, "must_change": True,
                                                "created_at": datetime.now().isoformat(timespec="seconds")}
                        elif isinstance(item, dict) and item.get("username"):
                            uname = str(item["username"]).strip()
                            if not uname: continue
                            users_map[uname] = {"password": _hash_password("1234"),
                                                "is_admin": bool(item.get("is_admin", uname=="admin")),
                                                "active": bool(item.get("active", True)),
                                                "must_change": True,
                                                "created_at": item.get("created_at", datetime.now().isoformat(timespec="seconds"))}
                    fixed = _bootstrap_admin({"users": users_map}); _save_users(fixed); return fixed
    except Exception: pass
    default = _bootstrap_admin({"users": {}}); _save_users(default); return default

def user_get(username: str) -> Optional[Dict[str, Any]]: return _load_users().get("users", {}).get(username)
def user_set(username: str, record: Dict[str, Any]) -> None:
    db = _load_users(); db.setdefault("users", {})[username] = record; _save_users(db)
def user_exists(username: str) -> bool: return user_get(username) is not None
def user_list() -> List[Dict[str, Any]]:
    db = _load_users(); out=[]
    for uname, rec in db.get("users", {}).items():
        r = dict(rec); r["username"]=uname; out.append(r)
    out.sort(key=lambda r:(not r.get("is_admin",False), r["username"])); return out
def user_delete(username: str) -> None:
    db = _load_users()
    if username in db.get("users", {}):
        if username == "admin": return
        db["users"].pop(username, None); _save_users(db)

def _auth_login_ui():
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>🔐 Entrar - 🏗️ Habisolute Analytics</div>", unsafe_allow_html=True)
    c1,c2,c3 = st.columns([1.3,1.3,0.7])
    with c1:
        user = st.text_input("Usuário", key="login_user", label_visibility="collapsed", placeholder="Usuário")
    with c2:
        pwd = st.text_input("Senha", key="login_pass", type="password",
                            label_visibility="collapsed", placeholder="Senha")
    with c3:
        st.markdown("<div style='height:2px'></div>", unsafe_allow_html=True)
        if st.button("Acessar", use_container_width=True):
            rec = user_get((user or "").strip())
            if not rec or not rec.get("active", True): st.error("Usuário inexistente ou inativo.")
            elif not _verify_password(pwd, rec.get("password","")): st.error("Senha incorreta.")
            else:
                s["logged_in"]=True; s["username"]=(user or "").strip()
                s["is_admin"]=bool(rec.get("is_admin",False)); s["must_change"]=bool(rec.get("must_change",False))
                prefs = load_user_prefs(); prefs["last_user"]=s["username"]; save_user_prefs(prefs)
                st.rerun()
    st.caption("Primeiro acesso: **admin / 1234** (será exigida troca de senha).")
    st.markdown("</div>", unsafe_allow_html=True)

def _force_change_password_ui(username: str):
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>🔑 Definir nova senha</div>", unsafe_allow_html=True)
    p1 = st.text_input("Nova senha", type="password"); p2 = st.text_input("Confirmar nova senha", type="password")
    if st.button("Salvar nova senha", use_container_width=True):
        if len(p1)<4: st.error("Use ao menos 4 caracteres.")
        elif p1!=p2: st.error("As senhas não conferem.")
        else:
            rec = user_get(username) or {}
            rec["password"]=_hash_password(p1); rec["must_change"]=False; user_set(username, rec)
            st.success("Senha atualizada! Redirecionando…"); s["must_change"]=False; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# Tela de login
# =============================================================================
if not s["logged_in"]:
    _auth_login_ui()
    st.stop()

# Troca obrigatória de senha
if s.get("must_change", False):
    _force_change_password_ui(s["username"])
    st.stop()

# >>> Cabeçalho apenas para quem está logado
_render_header()

# =============================================================================
# Toolbar de preferências
# =============================================================================
st.markdown("<div class='prefs-bar'>", unsafe_allow_html=True)
c1,c2,c3,c4 = st.columns([1.1,1.1,2.5,1.1])
with c1:
    s["theme_mode"] = st.radio("Tema", ["Escuro moderno","Claro corporativo"],
                              index=0 if s.get("theme_mode")=="Escuro moderno" else 1, horizontal=True)
with c2:
    s["brand"] = st.selectbox("🎨 Cor da marca", ["Laranja","Azul","Verde","Roxo"],
                              index=["Laranja","Azul","Verde","Roxo"].index(s.get("brand","Laranja")))
with c3:
    s["qr_url"] = st.text_input("URL do resumo (QR opcional na capa do PDF)", value=s.get("qr_url",""),
                                placeholder="https://exemplo.com/resumo")
with c4:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("💾 Salvar como padrão", use_container_width=True, key="k_save"):
            save_user_prefs({
                "theme_mode": s["theme_mode"], "brand": s["brand"], "qr_url": s["qr_url"],
                "last_user": s.get("username") or load_user_prefs().get("last_user","")
            })
            try:
                qp = st.query_params; qp.update({"theme": s["theme_mode"], "brand": s["brand"], "q": s["qr_url"]})
            except Exception: pass
            st.success("Preferências salvas! Dica: adicione esta página aos favoritos.")
    with col_b:
        if st.button("Sair", use_container_width=True, key="k_logout"):
            s["logged_in"] = False; st.rerun()
st.markdown("</div>", unsafe_allow_html=True)

# ---- Boas-vindas do usuário
nome_login = s.get("username") or load_user_prefs().get("last_user") or "—"
papel = "Admin" if s.get("is_admin") else "Usuário"
st.markdown(
    f"""
    <div style="margin:10px 0 4px 0; padding:10px 12px; border-radius:12px;
                border:1px solid var(--line); background:rgba(148,163,184,.10); font-weight:600;">
      👋 Olá, <b>{nome_login}</b> — <span style="opacity:.85">{papel}</span>
    </div>
    """,
    unsafe_allow_html=True
)

# =============================================================================
# Painel de Usuários (somente admin)
# =============================================================================
if s.get("is_admin", False):
    with st.expander("👤 Painel de Usuários (Admin)", expanded=False):
        st.markdown("Cadastre, ative/desative e redefina senhas dos usuários do sistema.")
        tab1, tab2 = st.tabs(["Usuários", "Novo usuário"])
        with tab1:
            users = user_list()
            if not users: st.info("Nenhum usuário cadastrado.")
            else:
                for u in users:
                    colA,colB,colC,colD,colE = st.columns([2,1,1.2,1.6,1.4])
                    colA.write(f"**{u['username']}**")
                    colB.write("👑 Admin" if u.get("is_admin") else "Usuário")
                    colC.write("✅ Ativo" if u.get("active", True) else "❌ Inativo")
                    colD.write(("Exige troca" if u.get("must_change") else "Senha OK"))
                    with colE:
                        if u["username"] != "admin":
                            if st.button(("Desativar" if u.get("active", True) else "Reativar"), key=f"act_{u['username']}"):
                                rec = user_get(u["username"]) or {}; rec["active"] = not rec.get("active", True)
                                user_set(u["username"], rec); st.rerun()
                            if st.button("Redefinir", key=f"rst_{u['username']}"):
                                rec = user_get(u["username"]) or {}; rec["password"] = _hash_password("1234"); rec["must_change"]=True
                                user_set(u["username"], rec); st.rerun()
                            if st.button("Excluir", key=f"del_{u['username']}"):
                                user_delete(u["username"]); st.rerun()
        with tab2:
            nu1,nu2,nu3 = st.columns([2,1,1])
            with nu1: new_user = st.text_input("Usuário (login)", key="new_user")
            with nu2: is_admin = st.checkbox("Administrador", value=False)
            with nu3: active = st.checkbox("Ativo", value=True)
            if st.button("Cadastrar usuário"):
                uname = (new_user or "").strip()
                if not uname: st.error("Informe o login do usuário.")
                elif user_exists(uname): st.error("Usuário já existe.")
                else:
                    user_set(uname, {"password": _hash_password("1234"), "is_admin": bool(is_admin),
                                     "active": bool(active), "must_change": True,
                                     "created_at": datetime.now().isoformat(timespec="seconds")})
                    st.success(f"Usuário **{uname}** criado com senha inicial **1234** (exigirá troca)."); st.rerun()

# =============================================================================
# >>> GUARDS & Sidebar
# =============================================================================
TOL_MP    = float(s.get("TOL_MP", 1.0))
BATCH_MODE = bool(s.get("BATCH_MODE", False))

with st.sidebar:
    st.markdown("### ⚙️ Opções do relatório")
    s["BATCH_MODE"] = st.toggle("Modo Lote (vários PDFs)", value=bool(s["BATCH_MODE"]))
    if s["BATCH_MODE"] != s["_prev_batch"]:
        s["_prev_batch"] = s["BATCH_MODE"]
        s["uploader_key"] += 1
    s["TOL_MP"] = st.slider("Tolerância Real × Estimado (MPa)", 0.0, 5.0, float(s["TOL_MP"]), 0.1)
    st.markdown("---")
    nome_login = s.get("username") or load_user_prefs().get("last_user") or "—"
    papel = "Admin" if s.get("is_admin") else "Usuário"
    st.caption(f"Usuário: **{nome_login}** ({papel})")

# =============================================================================
# Utilidades de parsing
# =============================================================================
def _limpa_horas(txt: str) -> str:
    txt = re.sub(r"\b\d{1,2}:\d{2}\b", "", txt)
    txt = re.sub(r"\bàs\s*\d{1,2}:\d{2}\b", "", txt, flags=re.I)
    return re.sub(r"\s{2,}", " ", txt).strip(" -•:;,.") 

def _limpa_usina_extra(txt: Optional[str]) -> Optional[str]:
    if not txt:
        return txt
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
            if m:
                return _limpa_usina_extra(m.group(1)) or _limpa_usina_extra(m.group(0))
            return _limpa_usina_extra(s0)
    for sline in linhas:
        if re.search(r"(?i)\busina\b", sline) or re.search(r"(?i)sa[ií]da da usina", sline):
            t = _limpa_horas(sline)
            t2 = re.sub(r"(?i)^.*\busina\b[:\-]?\s*", "", t).strip()
            if t2:
                return t2
            if t:
                return t
    return None

def _parse_abatim_nf_pair(tok: str) -> Tuple[Optional[float], Optional[float]]:
    if not tok:
        return None, None
    t = str(tok).strip().lower().replace("±", "+-").replace("mm", "").replace(",", ".")
    m = re.match(r"^\s*(\d+(?:\.\d+)?)(?:\s*\+?-?\s*(\d+(?:\.\d+)?))?\s*$", t)
    if not m:
        return None, None
    try:
        v = float(m.group(1))
        tol = float(m.group(2)) if m.group(2) is not None else None
        return v, tol
    except Exception:
        return None, None

def _detecta_abatimentos(linhas: List[str]) -> Tuple[Optional[float], Optional[float]]:
    abat_nf = None
    abat_obra = None
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
    """Extrai valores de fck presentes em uma linha."""
    if not line or "fck" not in line.lower():
        return []
    sanitized = line.replace(",", ".")
    parts = re.split(r"(?i)fck", sanitized)[1:]
    if not parts:
        return []

    values: List[float] = []
    age_with_suffix = re.compile(r"^(\d{1,3})(?:\s*(?:dias?|d))\b\s*[:=]?", re.I)
    age_plain       = re.compile(r"^(\d{1,3})\b\s*[:=]?", re.I)
    age_tokens = {3, 7, 14, 21, 28, 56, 63, 90}
    cut_keywords = (
        "mpa", "abatimento", "slump", "nota", "usina", "relatório", "relatorio",
        "consumo", "traço", "traco", "cimento", "dosagem"
    )

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
            if idx != -1:
                cut_at = min(cut_at, idx)
        seg = seg[:cut_at]

        for num in re.findall(r"\d+(?:\.\d+)?", seg):
            try:
                val = float(num)
            except ValueError:
                continue
            if 3 <= val <= 120 and val not in values:
                values.append(val)

    return values

def _to_float_or_none(value: Any) -> Optional[float]:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(val) else val

def _format_float_label(value: Optional[float]) -> str:
    if value is None or pd.isna(value):
        return "—"
    num = float(value)
    label = f"{num:.2f}".rstrip("0").rstrip(".")
    return label or f"{num:.2f}"

def _normalize_fck_label(value: Any) -> str:
    normalized = _to_float_or_none(value)
    if normalized is not None:
        return _format_float_label(normalized)
    raw = str(value).strip()
    if not raw or raw.lower() == 'nan':
        return "—"
    return raw

def extrair_dados_certificado(uploaded_file):
    """
    Retorna DataFrame com colunas:
      Relatório, CP, Idade (dias), Resistência (MPa), Nota Fiscal, Local, Usina,
      Abatimento NF (mm), Abatimento NF tol (mm), Abatimento Obra (mm)
    + metadados: obra, data_relatorio, fck_projeto
    """
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
                linhas_todas.extend([l.strip() for l in txt.split("\n") if l.strip()])
    except Exception:
        return (pd.DataFrame(columns=[
            "Relatório", "CP", "Idade (dias)", "Resistência (MPa)", "Nota Fiscal", "Local",
            "Usina", "Abatimento NF (mm)", "Abatimento NF tol (mm)", "Abatimento Obra (mm)"
        ]), "NÃO IDENTIFICADA", "NÃO IDENTIFICADA", "NÃO IDENTIFICADO")

    cp_regex = re.compile(r"^(?:[A-Z]{0,2})?\d{3,6}(?:\.\d{3})?$")
    data_regex = re.compile(r"\d{2}/\d{2}/\d{4}")
    data_token = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    tipo_token = re.compile(r"^A\d$", re.I)
    float_token = re.compile(r"^\d+[.,]\d+$")
    nf_regex = re.compile(r"^(?:\d{2,6}[.\-\/]?\d{3,6}|\d{5,12})$")
    pecas_regex = re.compile(r"(?i)peç[ac]s?\s+concretad[ao]s?:\s*(.*)")

    obra = "NÃO IDENTIFICADA"
    data_relatorio = "NÃO IDENTIFICADA"
    fck_projeto = "NÃO IDENTIFICADO"
    local_por_relatorio: Dict[str, str] = {}
    relatorio_atual = None
    fck_por_relatorio: Dict[str, List[float]] = {}
    fck_valores_globais: List[float] = []

    for sline in linhas_todas:
        if sline.startswith("Obra:"):
            obra = sline.replace("Obra:", "").strip().split(" Data")[0]
        m_data = data_regex.search(sline)
        if m_data and data_relatorio == "NÃO IDENTIFICADA":
            data_relatorio = m_data.group()
        if sline.startswith("Relatório:"):
            m_rel = re.search(r"Relatório:\s*(\d+)", sline)
            if m_rel:
                relatorio_atual = m_rel.group(1)
        m_pecas = pecas_regex.search(sline)
        if m_pecas and relatorio_atual:
            local_por_relatorio[relatorio_atual] = m_pecas.group(1).strip().rstrip(".")
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

                resistencia, res_idx = None, None
                if idade_idx is not None:
                    for j in range(idade_idx + 1, len(partes)):
                        t = partes[j]
                        if float_token.match(t):
                            resistencia = float(t.replace(",", "."))
                            res_idx = j; break

                if idade is None or resistencia is None:
                    continue

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
                            abat_nf_val = float(v)
                            abat_nf_tol = float(tol) if tol is not None else None
                            break

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
                if val_f not in uniques:
                    uniques.append(val_f)
            if uniques:
                rel_map[rel] = uniques[0]

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
            if fallback_fck is not None:
                fck_projeto = fallback_fck

        if rel_map or fallback_fck is not None:
            df["Relatório"] = df["Relatório"].astype(str)
            df["Fck Projeto"] = df["Relatório"].map(rel_map)
            if fallback_fck is not None:
                df["Fck Projeto"] = df["Fck Projeto"].fillna(fallback_fck)

    return df, obra, data_relatorio, fck_projeto
# app.py — Habisolute Analytics (login + painel + tema + capa QR + conflitos NF/CP + PDF/Excel)
# =============================================================================================

from __future__ import annotations

import io, re, json, base64, tempfile, zipfile, hashlib, warnings
from datetime import datetime, date
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
    Image as RLImage, PageBreak, KeepInFrame
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.graphics.barcode import qr as rl_qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import mm

# ===== Rodapé e numeração do PDF =====
FOOTER_TEXT = (
    "Estes resultados referem-se exclusivamente às amostras ensaiadas. "
    "Este documento poderá ser reproduzido somente na íntegra. "
    "Resultados apresentados sem considerar a incerteza de medição +- 0,90Mpa."
)
FOOTER_BRAND_TEXT = "Sistema Desenvolvido pela Habisolute Engenharia"

class NumberedCanvas(pdfcanvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs); self._saved_page_states = []
    def showPage(self): self._saved_page_states.append(dict(self.__dict__)); self._startPage()
    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer_and_pagenum(total_pages)
            super().showPage()
        super().save()
    def _wrap_footer(self, text, font_name="Helvetica", font_size=7, max_width=None):
        if max_width is None: max_width = self._pagesize[0] - 36 - 120
        words = text.split(); lines, line = [], ""
        for w in words:
            test = (line + " " + w).strip()
            if self.stringWidth(test, font_name, font_size) <= max_width: line = test
            else:
                if line: lines.append(line)
                line = w
        if line: lines.append(line)
        return lines
    def _draw_footer_and_pagenum(self, total_pages: int):
        w, h = self._pagesize
        self.setFont("Helvetica", 7)
        for i, ln in enumerate(self._wrap_footer(FOOTER_TEXT, "Helvetica", 7, w - 36 - 100)):
            y = 10 + i * 8
            if y > 20: break
            self.drawString(18, y, ln)
        self.setFont("Helvetica-Oblique", 8); self.drawCentredString(w/2.0, 26, FOOTER_BRAND_TEXT)
        self.setFont("Helvetica", 8); self.drawRightString(w - 18, 10, f"Página {self._pageNumber} de {total_pages}")

# =============================================================================
# Configuração básica
# =============================================================================
st.set_page_config(page_title="Habisolute — Relatórios", layout="wide")

PREFS_DIR = Path.home() / ".habisolute"; PREFS_DIR.mkdir(parents=True, exist_ok=True)
PREFS_PATH = PREFS_DIR / "prefs.json"; USERS_DB = PREFS_DIR / "users.json"

# ----- prefs util -----
def _save_all_prefs(data: Dict[str, Any]) -> None:
    tmp = PREFS_DIR / "prefs.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(PREFS_PATH)
def _load_all_prefs() -> Dict[str, Any]:
    try:
        if PREFS_PATH.exists(): return json.loads(PREFS_PATH.read_text(encoding="utf-8")) or {}
    except Exception: pass
    return {}
def load_user_prefs(key: str = "default") -> Dict[str, Any]: return _load_all_prefs().get(key, {})
def save_user_prefs(prefs: Dict[str, Any], key: str = "default") -> None:
    data = _load_all_prefs(); data[key] = prefs; _save_all_prefs(data)

# ===== Estado =====
s = st.session_state
s.setdefault("logged_in", False); s.setdefault("username", None); s.setdefault("is_admin", False)
s.setdefault("must_change", False)
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Claro corporativo"))
s.setdefault("brand", load_user_prefs().get("brand", "Laranja"))
s.setdefault("qr_url", load_user_prefs().get("qr_url", ""))
s.setdefault("uploader_key", 0); s.setdefault("OUTLIER_SIGMA", 3.0)
s.setdefault("TOL_MP", 1.0); s.setdefault("BATCH_MODE", False); s.setdefault("_prev_batch", s["BATCH_MODE"])
s.setdefault("ALLOW_PDF_WITH_CONFLICTS", False)

# Recupera usuário após refresh se necessário
if s.get("logged_in") and not s.get("username"):
    _p = load_user_prefs()
    if _p.get("last_user"): s["username"] = _p["last_user"]

# --- preferências via URL ---
def _apply_query_prefs():
    try:
        qp = st.query_params
        def _first(x):
            if x is None: return None
            return x[0] if isinstance(x, list) else x
        theme = _first(qp.get("theme") or qp.get("t"))
        brand = _first(qp.get("brand") or qp.get("b"))
        qr    = _first(qp.get("q") or qp.get("qr") or qp.get("u"))
        if theme in ("Escuro moderno","Claro corporativo"): s["theme_mode"] = theme
        if brand in ("Laranja","Azul","Verde","Roxo"): s["brand"] = brand
        if qr: s["qr_url"] = qr
    except Exception: pass
_apply_query_prefs()

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

plt.rcParams.update({"font.size":10,"axes.titlesize":12,"axes.labelsize":10,"axes.titleweight":"semibold","figure.autolayout":False})

if s["theme_mode"] == "Escuro moderno":
    plt.style.use("dark_background")
    css = f"""
    <style>
    :root {{
      --brand:{brand}; --brand-600:{brand600}; --brand-700:{brand700};
      --bg:#0b0f19; --panel:#0f172a; --surface:#111827; --text:#e5e7eb; --muted:#a3a9b7; --line:rgba(148,163,184,.18);
    }}
    .stApp, .main {{ background: var(--bg) !important; color: var(--text) !important; }}
    .block-container{{ padding-top: 56px; max-width: 1300px; }}
    .h-card{{ background: var(--panel); border:1px solid var(--line); border-radius:14px; padding:12px 14px; }}
    .h-kpi-label{{ font-size:12px; color:var(--muted) }} .h-kpi{{ font-size:22px; font-weight:800; }}
    .pill{{ display:inline-flex; gap:8px; padding:6px 10px; border-radius:999px; border:1px solid var(--line); background:rgba(148,163,184,.10); font-size:12.5px; }}
    .stButton > button, .stDownloadButton > button {{
      background: linear-gradient(180deg, {brand}, {brand600}) !important; color:#fff !important; border:0 !important; border-radius:12px !important;
      padding:12px 16px !important; font-weight:800 !important; box-shadow:0 8px 20px rgba(0,0,0,.18) !important;
    }}
    .stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] > div, .stMultiSelect div[data-baseweb="select"] > div, .stDateInput input {{
      background: var(--surface) !important; color: var(--text) !important; border-color: var(--line) !important;
    }}
    .stExpander > details > summary {{ background: var(--panel) !important; color: var(--text) !important; border:1px solid var(--line); border-radius:10px; padding:8px 12px; }}
    </style>
    """
else:
    plt.style.use("default")
    css = f"""
    <style>
    :root {{
      --brand:{brand}; --brand-600:{brand600}; --brand-700:{brand700};
      --bg:#f8fafc; --surface:#ffffff; --panel:#ffffff; --text:#0f172a; --muted:#475569; --line:rgba(2,6,23,.10);
    }}
    .stApp, .main {{ background: var(--bg) !important; color: var(--text) !important; }}
    .block-container{{ padding-top: 56px; max-width: 1300px; }}
    .h-card{{ background: var(--panel); border:1px solid var(--line); border-radius:14px; padding:12px 14px; }}
    .h-kpi-label{{ font-size:12px; color:var(--muted) }} .h-kpi{{ font-size:22px; font-weight:800; }}
    .pill{{ display:inline-flex; gap:8px; padding:6px 10px; border-radius:999px; border:1px solid var(--line); background:#fff; color:var(--text); font-size:12.5px; }}
    .stButton > button, .stDownloadButton > button {{
      background: linear-gradient(180deg, {brand}, {brand600}) !important; color:#fff !important; border:0 !important; border-radius:12px !important;
      padding:12px 16px !important; font-weight:800 !important; box-shadow:0 8px 20px rgba(0,0,0,.08) !important;
    }}
    .stTextInput input, .stNumberInput input, .stDateInput input {{ background:#fff !important; color:var(--text) !important; border:1px solid var(--line) !important; }}
    .stSelectbox div[data-baseweb="select"] > div, .stMultiSelect div[data-baseweb="select"] > div {{ background:#fff !important; color:var(--text) !important; border:1px solid var(--line) !important; }}
    .stExpander > details > summary {{ background:#fff !important; color:var(--text) !important; border:1px solid var(--line); border-radius:10px; padding:8px 12px; }}
    </style>
    """
st.markdown(css, unsafe_allow_html=True)

# -------- Cabeçalho (será mostrado só depois do login) ----------
def _render_header():
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown("<div class='app-header'><span class='brand-title' style='font-weight:800; font-size:22px; color: var(--text)'>🏗️ Habisolute IA</span></div>", unsafe_allow_html=True)
    st.caption("Envie certificados em PDF e gere análises, gráficos, KPIs e relatório final com capa personalizada.")

# =============================================================================
# Autenticação & gerenciamento de usuários
# =============================================================================
def _hash_password(pw: str) -> str: return hashlib.sha256(("habisolute|" + pw).encode("utf-8")).hexdigest()
def _verify_password(pw: str, hashed: str) -> bool:
    try: return _hash_password(pw) == hashed
    except Exception: return False

def _save_users(data: Dict[str, Any]) -> None:
    tmp = USERS_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(USERS_DB)
def _load_users() -> Dict[str, Any]:
    def _bootstrap_admin(db: Dict[str, Any]) -> Dict[str, Any]:
        db.setdefault("users", {})
        if "admin" not in db["users"]:
            db["users"]["admin"] = {
                "password": _hash_password("1234"), "is_admin": True, "active": True, "must_change": True,
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
                            users_map[uname] = {"password": _hash_password("1234"), "is_admin": (uname=="admin"),
                                                "active": True, "must_change": True,
                                                "created_at": datetime.now().isoformat(timespec="seconds")}
                        elif isinstance(item, dict) and item.get("username"):
                            uname = str(item["username"]).strip()
                            if not uname: continue
                            users_map[uname] = {"password": _hash_password("1234"),
                                                "is_admin": bool(item.get("is_admin", uname=="admin")),
                                                "active": bool(item.get("active", True)),
                                                "must_change": True,
                                                "created_at": item.get("created_at", datetime.now().isoformat(timespec="seconds"))}
                    fixed = _bootstrap_admin({"users": users_map}); _save_users(fixed); return fixed
    except Exception: pass
    default = _bootstrap_admin({"users": {}}); _save_users(default); return default

def user_get(username: str) -> Optional[Dict[str, Any]]: return _load_users().get("users", {}).get(username)
def user_set(username: str, record: Dict[str, Any]) -> None:
    db = _load_users(); db.setdefault("users", {})[username] = record; _save_users(db)
def user_exists(username: str) -> bool: return user_get(username) is not None
def user_list() -> List[Dict[str, Any]]:
    db = _load_users(); out=[]
    for uname, rec in db.get("users", {}).items():
        r = dict(rec); r["username"]=uname; out.append(r)
    out.sort(key=lambda r:(not r.get("is_admin",False), r["username"])); return out
def user_delete(username: str) -> None:
    db = _load_users()
    if username in db.get("users", {}):
        if username == "admin": return
        db["users"].pop(username, None); _save_users(db)

def _auth_login_ui():
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>🔐 Entrar - 🏗️ Habisolute Analytics</div>", unsafe_allow_html=True)
    c1,c2,c3 = st.columns([1.3,1.3,0.7])
    with c1:
        user = st.text_input("Usuário", key="login_user", label_visibility="collapsed", placeholder="Usuário")
    with c2:
        pwd = st.text_input("Senha", key="login_pass", type="password",
                            label_visibility="collapsed", placeholder="Senha")
    with c3:
        st.markdown("<div style='height:2px'></div>", unsafe_allow_html=True)
        if st.button("Acessar", use_container_width=True):
            rec = user_get((user or "").strip())
            if not rec or not rec.get("active", True): st.error("Usuário inexistente ou inativo.")
            elif not _verify_password(pwd, rec.get("password","")): st.error("Senha incorreta.")
            else:
                s["logged_in"]=True; s["username"]=(user or "").strip()
                s["is_admin"]=bool(rec.get("is_admin",False)); s["must_change"]=bool(rec.get("must_change",False))
                prefs = load_user_prefs(); prefs["last_user"]=s["username"]; save_user_prefs(prefs)
                st.rerun()
    st.caption("Primeiro acesso: **admin / 1234** (será exigida troca de senha).")
    st.markdown("</div>", unsafe_allow_html=True)

def _force_change_password_ui(username: str):
    st.markdown("<div class='login-card'>", unsafe_allow_html=True)
    st.markdown("<div class='login-title'>🔑 Definir nova senha</div>", unsafe_allow_html=True)
    p1 = st.text_input("Nova senha", type="password"); p2 = st.text_input("Confirmar nova senha", type="password")
    if st.button("Salvar nova senha", use_container_width=True):
        if len(p1)<4: st.error("Use ao menos 4 caracteres.")
        elif p1!=p2: st.error("As senhas não conferem.")
        else:
            rec = user_get(username) or {}
            rec["password"]=_hash_password(p1); rec["must_change"]=False; user_set(username, rec)
            st.success("Senha atualizada! Redirecionando…"); s["must_change"]=False; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# Tela de login
# =============================================================================
if not s["logged_in"]:
    _auth_login_ui()
    st.stop()

# Troca obrigatória de senha
if s.get("must_change", False):
    _force_change_password_ui(s["username"])
    st.stop()

# >>> Cabeçalho apenas para quem está logado
_render_header()

# =============================================================================
# Toolbar de preferências
# =============================================================================
st.markdown("<div class='prefs-bar'>", unsafe_allow_html=True)
c1,c2,c3,c4 = st.columns([1.1,1.1,2.5,1.1])
with c1:
    s["theme_mode"] = st.radio("Tema", ["Escuro moderno","Claro corporativo"],
                              index=0 if s.get("theme_mode")=="Escuro moderno" else 1, horizontal=True)
with c2:
    s["brand"] = st.selectbox("🎨 Cor da marca", ["Laranja","Azul","Verde","Roxo"],
                              index=["Laranja","Azul","Verde","Roxo"].index(s.get("brand","Laranja")))
with c3:
    s["qr_url"] = st.text_input("URL do resumo (QR opcional na capa do PDF)", value=s.get("qr_url",""),
                                placeholder="https://exemplo.com/resumo")
with c4:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("💾 Salvar como padrão", use_container_width=True, key="k_save"):
            save_user_prefs({
                "theme_mode": s["theme_mode"], "brand": s["brand"], "qr_url": s["qr_url"],
                "last_user": s.get("username") or load_user_prefs().get("last_user","")
            })
            try:
                qp = st.query_params; qp.update({"theme": s["theme_mode"], "brand": s["brand"], "q": s["qr_url"]})
            except Exception: pass
            st.success("Preferências salvas! Dica: adicione esta página aos favoritos.")
    with col_b:
        if st.button("Sair", use_container_width=True, key="k_logout"):
            s["logged_in"] = False; st.rerun()
st.markdown("</div>", unsafe_allow_html=True)

# ---- Boas-vindas do usuário
nome_login = s.get("username") or load_user_prefs().get("last_user") or "—"
papel = "Admin" if s.get("is_admin") else "Usuário"
st.markdown(
    f"""
    <div style="margin:10px 0 4px 0; padding:10px 12px; border-radius:12px;
                border:1px solid var(--line); background:rgba(148,163,184,.10); font-weight:600;">
      👋 Olá, <b>{nome_login}</b> — <span style="opacity:.85">{papel}</span>
    </div>
    """,
    unsafe_allow_html=True
)

# =============================================================================
# Painel de Usuários (somente admin)
# =============================================================================
if s.get("is_admin", False):
    with st.expander("👤 Painel de Usuários (Admin)", expanded=False):
        st.markdown("Cadastre, ative/desative e redefina senhas dos usuários do sistema.")
        tab1, tab2 = st.tabs(["Usuários", "Novo usuário"])
        with tab1:
            users = user_list()
            if not users: st.info("Nenhum usuário cadastrado.")
            else:
                for u in users:
                    colA,colB,colC,colD,colE = st.columns([2,1,1.2,1.6,1.4])
                    colA.write(f"**{u['username']}**")
                    colB.write("👑 Admin" if u.get("is_admin") else "Usuário")
                    colC.write("✅ Ativo" if u.get("active", True) else "❌ Inativo")
                    colD.write(("Exige troca" if u.get("must_change") else "Senha OK"))
                    with colE:
                        if u["username"] != "admin":
                            if st.button(("Desativar" if u.get("active", True) else "Reativar"), key=f"act_{u['username']}"):
                                rec = user_get(u["username"]) or {}; rec["active"] = not rec.get("active", True)
                                user_set(u["username"], rec); st.rerun()
                            if st.button("Redefinir", key=f"rst_{u['username']}"):
                                rec = user_get(u["username"]) or {}; rec["password"] = _hash_password("1234"); rec["must_change"]=True
                                user_set(u["username"], rec); st.rerun()
                            if st.button("Excluir", key=f"del_{u['username']}"):
                                user_delete(u["username"]); st.rerun()
        with tab2:
            nu1,nu2,nu3 = st.columns([2,1,1])
            with nu1: new_user = st.text_input("Usuário (login)", key="new_user")
            with nu2: is_admin = st.checkbox("Administrador", value=False)
            with nu3: active = st.checkbox("Ativo", value=True)
            if st.button("Cadastrar usuário"):
                uname = (new_user or "").strip()
                if not uname: st.error("Informe o login do usuário.")
                elif user_exists(uname): st.error("Usuário já existe.")
                else:
                    user_set(uname, {"password": _hash_password("1234"), "is_admin": bool(is_admin),
                                     "active": bool(active), "must_change": True,
                                     "created_at": datetime.now().isoformat(timespec="seconds")})
                    st.success(f"Usuário **{uname}** criado com senha inicial **1234** (exigirá troca)."); st.rerun()

# =============================================================================
# >>> GUARDS & Sidebar
# =============================================================================
TOL_MP    = float(s.get("TOL_MP", 1.0))
BATCH_MODE = bool(s.get("BATCH_MODE", False))

with st.sidebar:
    st.markdown("### ⚙️ Opções do relatório")
    s["BATCH_MODE"] = st.toggle("Modo Lote (vários PDFs)", value=bool(s["BATCH_MODE"]))
    if s["BATCH_MODE"] != s["_prev_batch"]:
        s["_prev_batch"] = s["BATCH_MODE"]
        s["uploader_key"] += 1
    s["TOL_MP"] = st.slider("Tolerância Real × Estimado (MPa)", 0.0, 5.0, float(s["TOL_MP"]), 0.1)
    st.markdown("---")
    nome_login = s.get("username") or load_user_prefs().get("last_user") or "—"
    papel = "Admin" if s.get("is_admin") else "Usuário"
    st.caption(f"Usuário: **{nome_login}** ({papel})")

# =============================================================================
# Utilidades de parsing
# =============================================================================
def _limpa_horas(txt: str) -> str:
    txt = re.sub(r"\b\d{1,2}:\d{2}\b", "", txt)
    txt = re.sub(r"\bàs\s*\d{1,2}:\d{2}\b", "", txt, flags=re.I)
    return re.sub(r"\s{2,}", " ", txt).strip(" -•:;,.") 

def _limpa_usina_extra(txt: Optional[str]) -> Optional[str]:
    if not txt:
        return txt
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
            if m:
                return _limpa_usina_extra(m.group(1)) or _limpa_usina_extra(m.group(0))
            return _limpa_usina_extra(s0)
    for sline in linhas:
        if re.search(r"(?i)\busina\b", sline) or re.search(r"(?i)sa[ií]da da usina", sline):
            t = _limpa_horas(sline)
            t2 = re.sub(r"(?i)^.*\busina\b[:\-]?\s*", "", t).strip()
            if t2:
                return t2
            if t:
                return t
    return None

def _parse_abatim_nf_pair(tok: str) -> Tuple[Optional[float], Optional[float]]:
    if not tok:
        return None, None
    t = str(tok).strip().lower().replace("±", "+-").replace("mm", "").replace(",", ".")
    m = re.match(r"^\s*(\d+(?:\.\d+)?)(?:\s*\+?-?\s*(\d+(?:\.\d+)?))?\s*$", t)
    if not m:
        return None, None
    try:
        v = float(m.group(1))
        tol = float(m.group(2)) if m.group(2) is not None else None
        return v, tol
    except Exception:
        return None, None

def _detecta_abatimentos(linhas: List[str]) -> Tuple[Optional[float], Optional[float]]:
    abat_nf = None
    abat_obra = None
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
    """Extrai valores de fck presentes em uma linha."""
    if not line or "fck" not in line.lower():
        return []
    sanitized = line.replace(",", ".")
    parts = re.split(r"(?i)fck", sanitized)[1:]
    if not parts:
        return []

    values: List[float] = []
    age_with_suffix = re.compile(r"^(\d{1,3})(?:\s*(?:dias?|d))\b\s*[:=]?", re.I)
    age_plain       = re.compile(r"^(\d{1,3})\b\s*[:=]?", re.I)
    age_tokens = {3, 7, 14, 21, 28, 56, 63, 90}
    cut_keywords = (
        "mpa", "abatimento", "slump", "nota", "usina", "relatório", "relatorio",
        "consumo", "traço", "traco", "cimento", "dosagem"
    )

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
            if idx != -1:
                cut_at = min(cut_at, idx)
        seg = seg[:cut_at]

        for num in re.findall(r"\d+(?:\.\d+)?", seg):
            try:
                val = float(num)
            except ValueError:
                continue
            if 3 <= val <= 120 and val not in values:
                values.append(val)

    return values

def _to_float_or_none(value: Any) -> Optional[float]:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(val) else val

def _format_float_label(value: Optional[float]) -> str:
    if value is None or pd.isna(value):
        return "—"
    num = float(value)
    label = f"{num:.2f}".rstrip("0").rstrip(".")
    return label or f"{num:.2f}"

def _normalize_fck_label(value: Any) -> str:
    normalized = _to_float_or_none(value)
    if normalized is not None:
        return _format_float_label(normalized)
    raw = str(value).strip()
    if not raw or raw.lower() == 'nan':
        return "—"
    return raw

def extrair_dados_certificado(uploaded_file):
    """
    Retorna DataFrame com colunas:
      Relatório, CP, Idade (dias), Resistência (MPa), Nota Fiscal, Local, Usina,
      Abatimento NF (mm), Abatimento NF tol (mm), Abatimento Obra (mm)
    + metadados: obra, data_relatorio, fck_projeto
    """
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
                linhas_todas.extend([l.strip() for l in txt.split("\n") if l.strip()])
    except Exception:
        return (pd.DataFrame(columns=[
            "Relatório", "CP", "Idade (dias)", "Resistência (MPa)", "Nota Fiscal", "Local",
            "Usina", "Abatimento NF (mm)", "Abatimento NF tol (mm)", "Abatimento Obra (mm)"
        ]), "NÃO IDENTIFICADA", "NÃO IDENTIFICADA", "NÃO IDENTIFICADO")

    cp_regex = re.compile(r"^(?:[A-Z]{0,2})?\d{3,6}(?:\.\d{3})?$")
    data_regex = re.compile(r"\d{2}/\d{2}/\d{4}")
    data_token = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    tipo_token = re.compile(r"^A\d$", re.I)
    float_token = re.compile(r"^\d+[.,]\d+$")
    nf_regex = re.compile(r"^(?:\d{2,6}[.\-\/]?\d{3,6}|\d{5,12})$")
    pecas_regex = re.compile(r"(?i)peç[ac]s?\s+concretad[ao]s?:\s*(.*)")

    obra = "NÃO IDENTIFICADA"
    data_relatorio = "NÃO IDENTIFICADA"
    fck_projeto = "NÃO IDENTIFICADO"
    local_por_relatorio: Dict[str, str] = {}
    relatorio_atual = None
    fck_por_relatorio: Dict[str, List[float]] = {}
    fck_valores_globais: List[float] = []

    for sline in linhas_todas:
        if sline.startswith("Obra:"):
            obra = sline.replace("Obra:", "").strip().split(" Data")[0]
        m_data = data_regex.search(sline)
        if m_data and data_relatorio == "NÃO IDENTIFICADA":
            data_relatorio = m_data.group()
        if sline.startswith("Relatório:"):
            m_rel = re.search(r"Relatório:\s*(\d+)", sline)
            if m_rel:
                relatorio_atual = m_rel.group(1)
        m_pecas = pecas_regex.search(sline)
        if m_pecas and relatorio_atual:
            local_por_relatorio[relatorio_atual] = m_pecas.group(1).strip().rstrip(".")
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

                resistencia, res_idx = None, None
                if idade_idx is not None:
                    for j in range(idade_idx + 1, len(partes)):
                        t = partes[j]
                        if float_token.match(t):
                            resistencia = float(t.replace(",", "."))
                            res_idx = j; break

                if idade is None or resistencia is None:
                    continue

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
                            abat_nf_val = float(v)
                            abat_nf_tol = float(tol) if tol is not None else None
                            break

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
                if val_f not in uniques:
                    uniques.append(val_f)
            if uniques:
                rel_map[rel] = uniques[0]

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
            if fallback_fck is not None:
                fck_projeto = fallback_fck

        if rel_map or fallback_fck is not None:
            df["Relatório"] = df["Relatório"].astype(str)
            df["Fck Projeto"] = df["Relatório"].map(rel_map)
            if fallback_fck is not None:
                df["Fck Projeto"] = df["Fck Projeto"].fillna(fallback_fck)

    return df, obra, data_relatorio, fck_projeto
        # =============================================================================
        # PDF — Cabeçalho completo + CAPA QR + gráficos + detalhamento CP
        # =============================================================================
        def _usina_label_from_df(df_: pd.DataFrame) -> str:
            if "Usina" not in df_.columns: return "—"
            seri = df_["Usina"].dropna().astype(str)
            if seri.empty: return "—"
            m = seri.mode()
            return str(m.iat[0]) if not m.empty else "—"

        def _abat_nf_header_label(df_: pd.DataFrame) -> str:
            snf = pd.to_numeric(df_.get("Abatimento NF (mm)"), errors="coerce").dropna()
            stol = pd.to_numeric(df_.get("Abatimento NF tol (mm)"), errors="coerce").dropna()
            if snf.empty:
                return "—"
            v = float(snf.mode().iloc[0])
            t = float(stol.mode().iloc[0]) if not stol.empty else 0.0
            return f"{v:.0f} ± {t:.0f} mm"

        def _doc_id() -> str:
            return "HAB-" + datetime.now().strftime("%Y%m%d-%H%M%S")

        def gerar_pdf(
            df: pd.DataFrame,
            stats: pd.DataFrame,
            fig1, fig2, fig3, fig4,
            obra_label: str, data_label: str, fck_label: str,
            verif_fck_df: Optional[pd.DataFrame],
            cond_df: Optional[pd.DataFrame],
            pareamento_df: Optional[pd.DataFrame],
            pv_cp_status: Optional[pd.DataFrame],
            qr_url: str,
            brand_color: str,
            brand600_color: str
        ) -> bytes:
            use_landscape = (len(df.columns) >= 8)
            pagesize = landscape(A4) if use_landscape else A4

            buffer = io.BytesIO()
            doc = SimpleDocTemplate(buffer, pagesize=pagesize,
                                    leftMargin=18, rightMargin=18, topMargin=26, bottomMargin=56)

            styles = getSampleStyleSheet()
            styles["Title"].fontName = "Helvetica-Bold"; styles["Title"].fontSize = 18
            styles["Heading2"].fontName = "Helvetica-Bold"; styles["Heading2"].fontSize = 14
            styles["Heading3"].fontName = "Helvetica-Bold"; styles["Heading3"].fontSize = 12
            styles["Normal"].fontName = "Helvetica"; styles["Normal"].fontSize = 9

            story = []

            # ===== CAPA =====
            title = Paragraph("Relatório Analítico — Corpos de Prova de Concreto", styles['Title'])
            subtitle = Paragraph(
                f"<font color='{brand_color}'>Habisolute Engenharia e Controle Tecnológico</font>",
                styles['Heading2']
            )

            usina_hdr = _usina_label_from_df(df)
            abat_nf_hdr = _abat_nf_header_label(df)
            cap_lines = [
                Paragraph(f"<b>Obra:</b> {obra_label}", styles['Normal']),
                Paragraph(f"<b>Período (datas dos certificados):</b> {data_label}", styles['Normal']),
                Paragraph(f"<b>fck de projeto:</b> {fck_label}", styles['Normal']),
                Paragraph(f"<b>Usina:</b> {usina_hdr}", styles['Normal']),
                Paragraph(f"<b>Abatimento de NF:</b> {abat_nf_hdr}", styles['Normal']),
            ]
            if qr_url:
                cap_lines.append(Paragraph(f"<b>Resumo (QR):</b> {qr_url}", styles['Normal']))

            left_block = KeepInFrame(400, 260, cap_lines, hAlign='LEFT')
            qr_flow = _qr_flowable(qr_url, size_mm=46) if qr_url else ""

            band = Table([[" "]], colWidths=[pagesize[0] - doc.leftMargin - doc.rightMargin])
            band.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(brand_color)),
                ("LINEBELOW", (0, 0), (-1, -1), 0.0, colors.HexColor(brand_color)),
                ("FONTSIZE", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]))

            cover = Table(
                [
                    [title, ""],
                    [subtitle, ""],
                    [left_block, qr_flow],
                ],
                colWidths=[pagesize[0] * 0.70 - doc.leftMargin, pagesize[0] * 0.30 - doc.rightMargin - 6],
            )
            cover.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]))

            story.append(band)
            story.append(Spacer(1, 10))
            story.append(cover)
            story.append(Spacer(1, 14))
            story.append(Paragraph(
                "<i>Este documento apresenta os resultados consolidados de rompimentos, gráficos e verificações do fck.</i>",
                styles['Normal']
            ))
            story.append(PageBreak())
            # ===== FIM CAPA =====

            # ===== Tabela principal
            headers = ["Relatório","CP","Idade (dias)","Resistência (MPa)","Nota Fiscal","Local","Usina","Abatimento NF (mm)","Abatimento Obra (mm)"]
            rows = df[headers].values.tolist()
            table = Table([headers] + rows, repeatRows=1)
            table.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                ("GRID",(0,0),(-1,-1),0.5,colors.black),
                ("ALIGN",(0,0),(-1,-1),"CENTER"),
                ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                ("FONTSIZE",(0,0),(-1,-1),8.5),
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                ("LEFTPADDING",(0,0),(-1,-1),3),("RIGHTPADDING",(0,0),(-1,-1),3),
                ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2),
            ]))
            story.append(table)
            story.append(Spacer(1, 8))

            # ===== Resumo estatístico
            if not stats.empty:
                stt = [["CP","Idade (dias)","Média","DP","n"]] + stats.values.tolist()
                t2 = Table(stt, repeatRows=1)
                t2.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.5,colors.black),
                    ("ALIGN",(0,0),(-1,-1),"CENTER"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.6),
                ]))
                story.append(Paragraph("Resumo Estatístico (Média + DP)", styles['Heading3']))
                story.append(t2)
                story.append(Spacer(1, 10))

            # ===== Gráficos
            def _img_from_fig_pdf(_fig, w=620, h=420):
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                _fig.savefig(tmp.name, dpi=200, bbox_inches="tight")
                return RLImage(tmp.name, width=w, height=h)

            if fig1: story.append(_img_from_fig_pdf(fig1, w=640, h=430)); story.append(Spacer(1, 8))
            if fig2: story.append(_img_from_fig_pdf(fig2, w=600, h=400)); story.append(Spacer(1, 8))
            if fig3: story.append(_img_from_fig_pdf(fig3, w=640, h=430)); story.append(Spacer(1, 8))
            if fig4: story.append(_img_from_fig_pdf(fig4, w=660, h=440)); story.append(Spacer(1, 8))

            # ===== Verificação do fck — tabelas
            if verif_fck_df is not None and not verif_fck_df.empty:
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
                tv.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.5,colors.black),
                    ("ALIGN",(0,0),(-2,-1),"CENTER"),
                    ("ALIGN",(-1,1),(-1,-1),"LEFT"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.6),
                ]))
                story.append(tv); story.append(Spacer(1, 8))

            if cond_df is not None and not cond_df.empty:
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
                story.append(tc); story.append(Spacer(1, 8))

            if pareamento_df is not None and not pareamento_df.empty:
                story.append(Paragraph("Pareamento ponto-a-ponto (Real × Estimado, sem médias)", styles["Heading3"]))
                head = ["CP","Idade (dias)","Real (MPa)","Estimado (MPa)","Δ","Status"]
                rows_p = pareamento_df[head].values.tolist()
                tp = Table([head] + rows_p, repeatRows=1)
                tp.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.5,colors.black),
                    ("ALIGN",(0,0),(-1,-1),"CENTER"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.6),
                ]))
                story.append(tp); story.append(Spacer(1, 8))

            # ===== Verificação detalhada por CP
            if pv_cp_status is not None and not pv_cp_status.empty:
                story.append(PageBreak())
                story.append(Paragraph("Verificação detalhada por CP (7/28/63 dias)", styles["Heading3"]))
                cols = list(pv_cp_status.columns)
                tab = [cols] + pv_cp_status.values.tolist()
                t_det = Table(tab, repeatRows=1)
                t_det.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.4,colors.black),
                    ("ALIGN",(0,0),(-1,-1),"CENTER"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.2),
                    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                    ("LEFTPADDING",(0,0),(-1,-1),2),("RIGHTPADDING",(0,0),(-1,-1),2),
                    ("TOPPADDING",(0,0),(-1,-1),1),("BOTTOMPADDING",(0,0),(-1,-1),1),
                ]))
                story.append(t_det)
                story.append(Spacer(1, 6))

            # ===== ID do documento
            story.append(Spacer(1, 10))
            story.append(Paragraph(f"<b>ID do documento:</b> {_doc_id()}", styles["Normal"]))

            doc.build(story, canvasmaker=NumberedCanvas)
            pdf = buffer.getvalue()
            buffer.close()
            return pdf

        # ===== PDF / Impressão / Exportações =====
        has_df = ("df_view" in locals()) and isinstance(df_view, pd.DataFrame) and (not df_view.empty)

        _conflicts_exist = (_has_nf or _has_cp)
        _pdf_allowed = True
        if _conflicts_exist and not bool(s.get("ALLOW_PDF_WITH_CONFLICTS", False)):
            _pdf_allowed = False

        if has_df:
            if _pdf_allowed:
                try:
                    pdf_bytes = gerar_pdf(
                        df_view, stats_cp_idade,
                        fig1, fig2, fig3, fig4,
                        str(df_view["Obra"].mode().iat[0]) if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty else "—",
                        data_label,
                        _format_float_label(fck_active),
                        verif_fck_df,
                        cond_df,
                        pareamento_df,
                        pv_cp_status,
                        s.get("qr_url",""),
                        brand,
                        brand600
                    )
                    _nome_pdf = "Relatorio_Graficos.pdf"
                    st.download_button("📄 Baixar Relatório (PDF)", data=pdf_bytes,
                                       file_name=_nome_pdf, mime="application/pdf")
                except Exception as e:
                    st.error(f"Falha ao gerar PDF: {e}")

                if "render_print_block" in globals() and "pdf_bytes" in locals():
                    try:
                        render_print_block(pdf_bytes, None, locals().get("brand", "#3b82f6"), locals().get("brand600", "#2563eb"))
                    except Exception:
                        pass
            else:
                st.warning("⛔ PDF e impressão bloqueados por conflitos entre relatórios (NF/CP). "
                           "Marque **‘Permitir PDF mesmo com conflitos’** para liberar.")

            # ===== Exportação: Excel (XLSX) e CSV (ZIP)
            try:
                stats_all_full = (
                    df_view.groupby("Idade (dias)")["Resistência (MPa)"]
                          .agg(mean="mean", std="std", count="count").reset_index()
                )

                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
                    df_view.to_excel(writer, sheet_name="Individuais", index=False)
                    stats_cp_idade.to_excel(writer, sheet_name="Médias_DP", index=False)

                    comp_df = stats_all_full.rename(columns={"mean": "Média Real", "std": "DP Real", "count": "n"})
                    _est_df = est_df if ('est_df' in locals()) else None
                    if isinstance(_est_df, pd.DataFrame) and (not _est_df.empty):
                        comp_df = comp_df.merge(
                            _est_df.rename(columns={"Resistência (MPa)": "Estimado"}),
                            on="Idade (dias)", how="outer"
                        ).sort_values("Idade (dias)")
                        comp_df.to_excel(writer, sheet_name="Comparação", index=False)
                    else:
                        comp_df.to_excel(writer, sheet_name="Comparação", index=False)

                    # Inserir figuras (melhor esforço)
                    try:
                        ws_md = writer.sheets.get("Médias_DP")
                        if ws_md is not None and "fig1" in locals() and fig1 is not None:
                            img1 = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                            fig1.savefig(img1.name, dpi=150, bbox_inches="tight")
                            ws_md.insert_image("H2", img1.name, {"x_scale": 0.7, "y_scale": 0.7})
                    except Exception:
                        pass
                    try:
                        ws_comp = writer.sheets.get("Comparação")
                        if ws_comp is not None and "fig2" in locals() and fig2 is not None:
                            img2 = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                            fig2.savefig(img2.name, dpi=150, bbox_inches="tight")
                            ws_comp.insert_image("H20", img2.name, {"x_scale": 0.7, "y_scale": 0.7})
                        if ws_comp is not None and "fig3" in locals() and fig3 is not None:
                            img3 = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                            fig3.savefig(img3.name, dpi=150, bbox_inches="tight")
                            ws_comp.insert_image("H38", img3.name, {"x_scale": 0.7, "y_scale": 0.7})
                    except Exception:
                        pass

                    # --- Planilhas de conflitos
                    try:
                        if 'confs' in locals():
                            if not confs["nf_conflicts"].empty:
                                confs["nf_conflicts"].to_excel(writer, sheet_name="Conflitos_NF", index=False)
                            if not confs["cp_conflicts"].empty:
                                confs["cp_conflicts"].to_excel(writer, sheet_name="Conflitos_CP", index=False)
                    except Exception:
                        pass

                st.download_button(
                    "📊 Baixar Excel (XLSX)",
                    data=excel_buffer.getvalue(),
                    file_name="Relatorio_Graficos.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

                zip_buf = io.BytesIO()
                with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("Individuais.csv", df_view.to_csv(index=False, sep=";"))
                    z.writestr("Medias_DP.csv", stats_cp_idade.to_csv(index=False, sep=";"))
                    if isinstance(_est_df, pd.DataFrame) and (not _est_df.empty):
                        z.writestr("Estimativas.csv", _est_df.to_csv(index=False, sep=";"))
                    if 'comp_df' in locals():
                        z.writestr("Comparacao.csv", comp_df.to_csv(index=False, sep=";"))
                    # Conflitos CSV (útil para auditoria rápida)
                    try:
                        if not confs["nf_conflicts"].empty:
                            z.writestr("Conflitos_NF.csv", confs["nf_conflicts"].to_csv(index=False, sep=";"))
                        if not confs["cp_conflicts"].empty:
                            z.writestr("Conflitos_CP.csv", confs["cp_conflicts"].to_csv(index=False, sep=";"))
                    except Exception:
                        pass

                st.download_button(
                    "🗃️ Baixar CSVs (ZIP)",
                    data=zip_buf.getvalue(),
                    file_name="Relatorio_Graficos_CSVs.zip",
                    mime="application/zip",
                    use_container_width=True
                )
            except Exception:
                pass
else:
    st.info("Envie um PDF para visualizar os gráficos, relatório e exportações.")

# 5) Ler Novo(s) Certificado(s)
if st.button("📂 Ler Novo(s) Certificado(s)", use_container_width=True, key="btn_novo"):
    s["uploader_key"] += 1
    st.rerun()

st.markdown("</div>", unsafe_allow_html=True)
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
