# app.py — Habisolute Analytics (login + painel + tema + header + pipeline + validações + auditoria)
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
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas as pdfcanvas

# ===== Rodapé e numeração do PDF =====
FOOTER_TEXT = (
    "Estes resultados referem-se exclusivamente às amostras ensaiadas. "
    "Este documento poderá ser reproduzido somente na íntegra. "
    "Resultados apresentados sem considerar a incerteza de medição +- 0,90Mpa."
)
FOOTER_BRAND_TEXT = "Sistema Desenvolvido por IA e pela Habisolute Engenharia"

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

s = st.session_state
s.setdefault("logged_in", False); s.setdefault("username", None); s.setdefault("is_admin", False)
s.setdefault("must_change", False)
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Claro corporativo"))
s.setdefault("brand", load_user_prefs().get("brand", "Laranja"))
s.setdefault("qr_url", load_user_prefs().get("qr_url", ""))
s.setdefault("uploader_key", 0); s.setdefault("OUTLIER_SIGMA", 3.0)
s.setdefault("TOL_MP", 1.0); s.setdefault("BATCH_MODE", False); s.setdefault("_prev_batch", s["BATCH_MODE"])

if s.get("logged_in") and not s.get("username"):
    _p = load_user_prefs()
    if _p.get("last_user"): s["username"] = _p["last_user"]

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

s.setdefault("wide_layout", True)
MAX_W = 1800 if s.get("wide_layout") else 1300

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
      --bg:#0b0f19; --panel:#0f172a; --surface:#111827; --text:#e5e7eb; --muted:#a3a9b7; --line:rgba(148,163,184,.18);
    }}
    .stApp, .main {{ background: var(--bg) !important; color: var(--text) !important; }}
    .block-container{{ padding-top:56px; max-width: {MAX_W}px; }}
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
    .block-container{{ padding-top:56px; max-width: {MAX_W}px; }}
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

def _render_header():
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown("<div class='app-header'><span class='brand-title' style='font-weight:800; font-size:22px; color: var(--text)'>🏗️ Habisolute IA</span></div>", unsafe_allow_html=True)
    st.caption("Envie certificados em PDF e gere análises, gráficos, KPIs e relatório final com capa personalizada.")

# ====== autenticação (igual ao seu) ======
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
            if not rec or not rec.get("active", True):
                st.error("Usuário inexistente ou inativo.")
                log_event("login_fail", {"username": user, "reason": "not_found_or_inactive"}, level="WARN")
            elif not _verify_password(pwd, rec.get("password","")):
                st.error("Senha incorreta.")
                log_event("login_fail", {"username": user, "reason": "bad_password"}, level="WARN")
            else:
                s["logged_in"]=True; s["username"]=(user or "").strip()
                s["is_admin"]=bool(rec.get("is_admin",False)); s["must_change"]=bool(rec.get("must_change",False))
                prefs = load_user_prefs(); prefs["last_user"]=s["username"]; save_user_prefs(prefs)
                log_event("login_success", {"username": s["username"]})
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
            log_event("password_changed", {"username": username})
            st.success("Senha atualizada! Redirecionando…"); s["must_change"]=False; st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

if not s["logged_in"]:
    _auth_login_ui()
    st.stop()

if s.get("must_change", False):
    _force_change_password_ui(s["username"])
    st.stop()

_render_header()
# =============================================================================
# Sidebar (opções do relatório)
# =============================================================================
with st.sidebar:
    st.markdown("### ⚙️ Opções do relatório")

    s["wide_layout"] = st.toggle(
        "Tela larga (1800px)",
        value=bool(s.get("wide_layout", True)),
        key="opt_wide_layout",
    )

    s["BATCH_MODE"] = st.toggle(
        "Modo Lote (vários PDFs)",
        value=bool(s["BATCH_MODE"]),
        key="opt_batch_mode",
    )

    if s["BATCH_MODE"] != s["_prev_batch"]:
        s["_prev_batch"] = s["BATCH_MODE"]
        s["uploader_key"] += 1

    s["TOL_MP"] = st.slider(
        "Tolerância Real × Estimado (MPa)",
        0.0, 5.0, float(s["TOL_MP"]), 0.1,
        key="opt_tol_mpa",
    )

    st.markdown("---")
    nome_login = s.get("username") or load_user_prefs().get("last_user") or "—"
    papel = "Admin" if s.get("is_admin") else "Usuário"
    st.caption(f"Usuário: **{nome_login}** ({papel})")

# =============================================================================
# Funções utilitárias de parsing (iguais às suas)
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
    age_tokens = {3, 7, 14, 21, 28, 56, 63, 90}
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
    num = float(value); label = f"{num:.2f}".rstrip("0").rstrip(".")
    return label or f"{num:.2f}"

def _normalize_fck_label(value: Any) -> str:
    normalized = _to_float_or_none(value)
    if normalized is not None: return _format_float_label(normalized)
    raw = str(value).strip()
    if not raw or raw.lower() == 'nan': return "—"
    return raw

def extrair_dados_certificado(uploaded_file):
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
            "Usina","Abatimento NF (mm)","Abatimento NF tol (mm)","Abatimento Obra (mm)"
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
                    relatorio, cp, idade, resistência, nf, local,
                    usina_nome,
                    (abat_nf_val if abat_nf_val is not None else abat_nf_pdf),
                    abat_nf_tol,
                    (abat_obra_val if abat_obra_val is not None else abat_obra_pdf)
                ])
            except Exception:
                pass

    df = pd.DataFrame(dados, columns=[
        "Relatório","CP","Idade (dias)","Resistência (MPa)","Nota Fiscal","Local",
        "Usina","Abatimento NF (mm)","Abatimento NF tol (mm)","Abatimento Obra (mm)"
    ])

    if not df.empty:
        rel_map = {}
        for rel, valores in fck_por_relatorio.items():
            uniques = []
            for valor in valores:
                try:
                    val_f = float(valor)
                except Exception:
                    continue
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
# KPIs e visão geral
# =============================================================================
def compute_exec_kpis(df_view: pd.DataFrame, fck_val: Optional[float]):
    def _pct_hit(age):
        if fck_val is None or pd.isna(fck_val): return None
        g = df_view[df_view["Idade (dias)"] == age].groupby("CP")["Resistência (MPa)"].mean()
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
# Cabeçalho e uploader
# =============================================================================
st.caption("Envie certificados em PDF e gere análises, gráficos, KPIs e relatório final com capa personalizada.")
_uploader_key = f"uploader_{'multi' if s['BATCH_MODE'] else 'single'}_{s['uploader_key']}"
if s["BATCH_MODE"]:
    uploaded_files = st.file_uploader("📁 PDF(s)", type=["pdf"], accept_multiple_files=True,
                                      key=_uploader_key, help="Carregue 1 PDF (ou vários em modo lote).")
else:
    up1 = st.file_uploader("📁 PDF (1 arquivo)", type=["pdf"], accept_multiple_files=False,
                           key=_uploader_key, help="Carregue 1 PDF (ou vários em modo lote).")
    uploaded_files = [up1] if up1 is not None else []

# Helpers de nome de arquivo
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
# Pipeline principal
# =============================================================================
if uploaded_files:
    frames = []
    for f in uploaded_files:
        if f is None: continue
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

    if not frames:
        st.error("⚠️ Não encontrei CPs válidos nos PDFs enviados.")
    else:
        df = pd.concat(frames, ignore_index=True)

        # Filtros
        st.markdown("#### Filtros")
        rels = sorted(df["Relatório"].astype(str).unique())
        sel_rels = st.multiselect("Relatórios", rels, default=rels)

        def to_date(d):
            try: return datetime.strptime(str(d), "%d/%m/%Y").date()
            except Exception: return None

        df["_DataObj"] = df["Data Certificado"].apply(to_date)
        valid_dates = [d for d in df["_DataObj"] if d is not None]
        if valid_dates:
            dmin, dmax = min(valid_dates), max(valid_dates)
            dini, dfim = st.date_input("Intervalo de data do certificado", (dmin, dmax))
        else:
            dini, dfim = None, None

        if st.button("🔄 Limpar filtros / Novo upload"):
            s["uploader_key"] += 1
            st.rerun()

        mask = df["Relatório"].astype(str).isin(sel_rels)
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

        if df_view.empty:
            st.info("Nenhum dado disponível para o fck selecionado.")
            st.stop()

        # Estatísticas por CP/Idade
        stats_cp_idade = (
            df_view.groupby(["CP", "Idade (dias)"])["Resistência (MPa)"]
                  .agg(Média="mean", Desvio_Padrão="std", n="count").reset_index()
        )

        # VISÃO GERAL
        # (reusa sua função anterior se quiser; aqui simplificado)
        fck_series_all = pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
        fck_active = float(fck_series_all.mode().iloc[0]) if not fck_series_all.empty else None

        st.markdown("#### ✅ Verificação do fck de Projeto")
        # médias por idade (7/28/63)
        mean_by_age = df_view.groupby("Idade (dias)")["Resistência (MPa)"].mean()
        m7  = mean_by_age.get(7,  float("nan"))
        m28 = mean_by_age.get(28, float("nan"))
        m63 = mean_by_age.get(63, float("nan"))
        verif_fck_df = pd.DataFrame({
            "Idade (dias)": [7, 28, 63],
            "Média Real (MPa)": [m7, m28, m63],
            "fck Projeto (MPa)": [float("nan"),
                                  (fck_active if fck_active is not None else float("nan")),
                                  (fck_active if fck_active is not None else float("nan"))],
        })
        resumo_status = []
        for idade, media, fckp in verif_fck_df.itertuples(index=False):
            if idade == 7:
                resumo_status.append("🟡 Informativo (7d)")
            else:
                if pd.isna(media) or pd.isna(fckp):
                    resumo_status.append("⚪ Sem dados")
                else:
                    resumo_status.append("🟢 Atingiu fck" if float(media) >= float(fckp) else "🔴 Não atingiu fck")
        verif_fck_df["Status"] = resumo_status
        st.dataframe(verif_fck_df, use_container_width=True)

        # ===== Verificação detalhada por CP (7/28/63) — aqui nasce o pv_cp_status ====
        st.markdown("#### ✅ Verificação detalhada por CP (7/28/63 dias)")
        tmp_v = df_view[df_view["Idade (dias)"].isin([7, 28, 63])].copy()
        pv_cp_status = None
        if tmp_v.empty:
            st.info("Sem CPs de 7/28/63 dias no filtro atual.")
        else:
            tmp_v["MPa"] = pd.to_numeric(tmp_v["Resistência (MPa)"], errors="coerce")
            tmp_v["rep"] = tmp_v.groupby(["CP", "Idade (dias)"]).cumcount() + 1

            pv_multi = tmp_v.pivot_table(index="CP", columns=["Idade (dias)", "rep"], values="MPa", aggfunc="first").sort_index(axis=1)
            for age in [7, 28, 63]:
                if age not in pv_multi.columns.get_level_values(0):
                    pv_multi[(age, 1)] = pd.NA

            ordered = []
            for age in [7, 28, 63]:
                reps = sorted([r for (a, r) in pv_multi.columns if a == age])
                for r in reps: ordered.append((age, r))
            pv_multi = pv_multi.reindex(columns=ordered)

            def _flat(age, rep):
                base = f"{age}d"
                return f"{base} (MPa)" if rep == 1 else f"{base} #{rep} (MPa)"

            pv = pv_multi.copy(); pv.columns = [_flat(a, r) for (a, r) in pv_multi.columns]
            pv = pv.reset_index()

            try:
                pv["__cp_sort__"] = pv["CP"].astype(str).str.extract(r"(\d+)").astype(float)
            except Exception:
                pv["__cp_sort__"] = range(len(pv))
            pv = pv.sort_values(["__cp_sort__", "CP"]).drop(columns="__cp_sort__", errors="ignore")

            fck_series_focus2 = pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
            fck_active2 = float(fck_series_focus2.mode().iloc[0]) if not fck_series_focus2.empty else None

            def _status_text_media(media_idade, age, fckp):
                if pd.isna(media_idade) or (fckp is None) or pd.isna(fckp): return "⚪ Sem dados"
                if age == 7: return "🟡 Informativo (7d)"
                return "🟢 Atingiu fck" if float(media_idade) >= float(fckp) else "🔴 Não atingiu fck"

            media_7  = pv_multi[7].mean(axis=1)  if 7  in pv_multi.columns.get_level_values(0) else pd.Series(pd.NA, index=pv_multi.index)
            media_63 = pv_multi[63].mean(axis=1) if 63 in pv_multi.columns.get_level_values(0) else pd.Series(pd.NA, index=pv_multi.index)

            if 28 in pv_multi.columns.get_level_values(0) and (fck_active2 is not None) and not pd.isna(fck_active2):
                cols28 = pv_multi[28]
                def _all_reps_ok(row):
                    vals = row.dropna().astype(float)
                    if vals.empty: return None
                    return bool((vals >= float(fck_active2)).all())
                ok28 = cols28.apply(_all_reps_ok, axis=1)
            else:
                ok28 = pd.Series([None] * pv_multi.shape[0], index=pv_multi.index)

            def _status_from_ok(ok):
                if ok is None: return "⚪ Sem dados"
                return "🟢 Atingiu fck" if ok else "🔴 Não atingiu fck"

            status_df = pd.DataFrame({
                "Status 7d":  [ _status_text_media(v, 7,  fck_active2) for v in media_7.reindex(pv_multi.index) ],
                "Status 28d": [ _status_from_ok(v) for v in ok28.reindex(pv_multi.index) ],
                "Status 63d": [ _status_text_media(v, 63, fck_active2) for v in media_63.reindex(pv_multi.index) ],
            }, index=pv_multi.index)

            def _delta_flag(row_vals: pd.Series) -> bool:
                vals = pd.to_numeric(row_vals.dropna(), errors="coerce").dropna().astype(float)
                if vals.empty: return False
                return (vals.max() - vals.min()) > 2.0

            alerta_pares = []
            for idx in pv_multi.index:
                flag = False
                for age in [7, 28, 63]:
                    cols = [c for c in pv_multi.columns if c[0] == age]
                    if not cols: continue
                    series_age = pv_multi.loc[idx, cols]
                    if _delta_flag(series_age):
                        flag = True; break
                alerta_pares.append("🟠 Δ pares > 2 MPa" if flag else "")

            pv = pv.merge(status_df, left_on="CP", right_index=True, how="left")
            pv["Alerta Pares (Δ>2 MPa)"] = alerta_pares

            cols_cp = ["CP"]
            cols_7  = [c for c in pv.columns if c.startswith("7d")]
            cols_28 = [c for c in pv.columns if c.startswith("28d")]
            cols_63 = [c for c in pv.columns if c.startswith("63d")]

            ordered_cols = (
                cols_cp + cols_7 + (["Status 7d"] if "Status 7d" in pv.columns else []) +
                cols_28 + (["Status 28d"] if "Status 28d" in pv.columns else []) +
                cols_63 + (["Status 63d"] if "Status 63d" in pv.columns else []) +
                ["Alerta Pares (Δ>2 MPa)"]
            )
            pv = pv[ordered_cols].rename(columns={
                "Status 7d":"7 dias — Status",
                "Status 28d":"28 dias — Status",
                "Status 63d":"63 dias — Status"
            })
            pv_cp_status = pv.copy()
            st.dataframe(pv_cp_status, use_container_width=True)

        # =============================================================================
        # PDF — com condição nova no título da verificação detalhada
        # =============================================================================
        def gerar_pdf(df: pd.DataFrame,
                      stats: pd.DataFrame,
                      verif_fck_df: Optional[pd.DataFrame],
                      pv_cp_status: Optional[pd.DataFrame],
                      obra_label: str,
                      periodo_label: str,
                      fck_label: str,
                      qr_url: str) -> bytes:
            from reportlab.lib import colors as _C
            import io, tempfile
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
            story.append(Paragraph("Relatório de Rompimento de Corpos de Prova", styles['Heading2']))
            story.append(Paragraph(f"Obra: {obra_label}", styles['Normal']))
            story.append(Paragraph(f"Período (datas dos certificados): {periodo_label}", styles['Normal']))
            story.append(Paragraph(f"fck de projeto: {fck_label}", styles['Normal']))
            if qr_url:
                story.append(Paragraph(f"Resumo/QR: {qr_url}", styles['Normal']))
            story.append(Spacer(1, 8))

            headers = ["Relatório","CP","Idade (dias)","Resistência (MPa)","Nota Fiscal","Local","Usina","Abatimento NF (mm)","Abatimento Obra (mm)"]
            rows = df[headers].values.tolist()
            table = Table([headers] + rows, repeatRows=1)
            table.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,0),_C.lightgrey),
                ("GRID",(0,0),(-1,-1),0.5,_C.black),
                ("ALIGN",(0,0),(-1,-1),"CENTER"),
                ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                ("FONTSIZE",(0,0),(-1,-1),8.5),
            ]))
            story.append(table); story.append(Spacer(1, 8))

            if verif_fck_df is not None and not verif_fck_df.empty:
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
                    ("BACKGROUND",(0,0),(-1,0),_C.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.5,_C.black),
                    ("ALIGN",(0,0),(-2,-1),"CENTER"),
                    ("ALIGN",(-1,1),(-1,-1),"LEFT"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.6),
                ]))
                story.append(tv); story.append(Spacer(1, 8))

            # ===== AQUI ENTRA SUA CONDIÇÃO NOVA =====
            if pv_cp_status is not None and not pv_cp_status.empty:
                story.append(PageBreak())

                _fck_txt = fck_label or "—"
                if "mpa" not in _fck_txt.lower():
                    _fck_txt = f"{_fck_txt} MPa"

                story.append(Paragraph(
                    f"""<para>
                        <b>Verificação detalhada por CP (7/28/63 dias)</b>
                        &nbsp;
                        <font backcolor="#facc15" color="#000000"><b>fck de projeto: {_fck_txt}</b></font>
                    </para>""",
                    styles["Heading3"]
                ))

                cols = list(pv_cp_status.columns)
                tab  = [cols] + pv_cp_status.values.tolist()
                t_det = Table(tab, repeatRows=1)
                t_det.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),_C.lightgrey),
                    ("GRID",(0,0),(-1,-1),0.4,_C.black),
                    ("ALIGN",(0,0),(-1,-1),"CENTER"),
                    ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                    ("FONTSIZE",(0,0),(-1,-1),8.2),
                    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                    ("LEFTPADDING",(0,0),(-1,-1),2),("RIGHTPADDING",(0,0),(-1,-1),2),
                    ("TOPPADDING",(0,0),(-1,-1),1),("BOTTOMPADDING",(0,0),(-1,-1),1),
                ]))

                def _idx(name):
                    try: return cols.index(name)
                    except ValueError: return -1
                i_s7    = _idx("7 dias — Status")
                i_s28   = _idx("28 dias — Status")
                i_s63   = _idx("63 dias — Status")
                i_alert = _idx("Alerta Pares (Δ>2 MPa)")

                # pintar status
                data_rows = tab[1:]
                def _status_bg(text: str):
                    t = str(text or "").lower()
                    if "informativo" in t: return _C.HexColor("#facc15")
                    if "não atingiu" in t or "nao atingiu" in t: return _C.HexColor("#ef4444")
                    if "atingiu" in t: return _C.HexColor("#16a34a")
                    if "sem dados" in t: return _C.HexColor("#e5e7eb")
                    return None
                ts_extra = []
                for r, row in enumerate(data_rows, start=1):
                    for c in [i_s7, i_s28, i_s63]:
                        if c is None or c < 0: continue
                        bg = _status_bg(row[c])
                        if bg:
                            ts_extra.append(("BACKGROUND", (c, r), (c, r), bg))
                            ts_extra.append(("TEXTCOLOR",  (c, r), (c, r), _C.black))
                            ts_extra.append(("FONTNAME",   (c, r), (c, r), "Helvetica-Bold"))
                    if i_alert is not None and i_alert >= 0:
                        if "Δ" in str(row[i_alert]):
                            ts_extra.append(("BACKGROUND", (i_alert, r), (i_alert, r), _C.HexColor("#f97316")))
                            ts_extra.append(("TEXTCOLOR",  (i_alert, r), (i_alert, r), _C.black))
                            ts_extra.append(("FONTNAME",   (i_alert, r), (i_alert, r), "Helvetica-Bold"))
                if ts_extra:
                    t_det.setStyle(TableStyle(ts_extra))

                story.append(t_det); story.append(Spacer(1, 6))

            # ID do documento
            story.append(Spacer(1, 10))
            story.append(Paragraph(f"<b>ID do documento:</b> HAB-{datetime.now().strftime('%Y%m%d-%H%M%S')}", styles["Normal"]))

            doc.build(story, canvasmaker=NumberedCanvas)
            pdf = buffer.getvalue()
            buffer.close()
            return pdf

        # ===== gerar e baixar =====
        if s.get("is_admin", False):
            datas_validas = [d for d in df["_DataObj"].dropna().tolist()] if "_DataObj" in df.columns else []
            periodo_label = "—"
            if datas_validas:
                di, df_ = min(datas_validas), max(datas_validas)
                periodo_label = di.strftime('%d/%m/%Y') if di == df_ else f"{di.strftime('%d/%m/%Y')} — {df_.strftime('%d/%m/%Y')}"
            pdf_bytes = gerar_pdf(
                df_view,
                stats_cp_idade,
                verif_fck_df,
                pv_cp_status,
                str(df_view["Obra"].mode().iat[0]) if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty else "—",
                periodo_label,
                _format_float_label(fck_active),
                s.get("qr_url","")
            )
            file_name_pdf = build_pdf_filename(df_view, uploaded_files)
            st.download_button("📄 Baixar Relatório (PDF)", data=pdf_bytes,
                               file_name=file_name_pdf, mime="application/pdf", use_container_width=True)
            try: render_print_block(pdf_bytes, None, brand, brand600)
            except Exception: pass

else:
    st.info("Envie um PDF para visualizar os gráficos, relatório e exportações.")

# Rodapé
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
