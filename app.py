# app.py — Habisolute Analytics (com segurança, PDF melhorado e verificação detalhada no relatório)
# Requisitos: streamlit, pandas, pdfplumber, matplotlib, reportlab, xlsxwriter

import os, io, re, json, base64, tempfile, zipfile, secrets, hashlib, hmac
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

# =============================================================================
# Rodapé e numeração do PDF
# =============================================================================
FOOTER_TEXT = (
    "Estes resultados referem-se exclusivamente às amostras ensaiadas, portanto esse documento poderá ser "
    "reproduzido somente na íntegra. Resultados sem considerar a incerteza da medição."
)
FOOTER_BRAND = "Sistema Desenvolvido pela Habisolute Engenharia"

class NumberedCanvas(pdfcanvas.Canvas):
    """Canvas que adiciona 'Página X de Y' e o rodapé legal em todas as páginas."""
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
            max_width = self._pagesize[0] - 36 - 140
        words = text.split()
        lines, line = [], ""
        for w in words:
            test = (line + " " + w).strip()
            if self.stringWidth(test, font_name, font_size) <= max_width:
                line = test
            else:
                if line: lines.append(line)
                line = w
        if line: lines.append(line)
        return lines

    def _draw_footer_and_pagenum(self, total_pages: int):
        w, h = self._pagesize
        text_font, text_size, leading = "Helvetica", 7, 8
        right_reserve = 95

        # Linha separadora leve
        self.setStrokeColorRGB(0.7, 0.7, 0.7)
        self.setLineWidth(0.3)
        self.line(18, 28, w - 18, 28)

        # Rodapé legal
        self.setFont(text_font, text_size)
        lines = self._wrap_footer(FOOTER_TEXT, font_name=text_font, font_size=text_size, max_width=w - 36 - right_reserve)
        base_y = 10
        for i, ln in enumerate(lines):
            y = base_y + i * leading
            if y > 28 - leading: break
            self.drawString(18, y, ln)

        # Marca de autoria
        self.setFont("Helvetica-Oblique", 7)
        self.drawString(18, 30, FOOTER_BRAND)

        # Número de página
        self.setFont("Helvetica", 8)
        self.drawRightString(w - 18, 10, f"Página {self._pageNumber} de {total_pages}")

# =============================================================================
# Configuração básica
# =============================================================================
st.set_page_config(page_title="Habisolute — Relatórios", layout="wide")

PREFS_DIR = Path.home() / ".habisolute"
PREFS_DIR.mkdir(parents=True, exist_ok=True)
PREFS_PATH = PREFS_DIR / "prefs.json"

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

# Estado
s = st.session_state
s.setdefault("logged_in", False)
s.setdefault("current_user", None)
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Claro corporativo"))
s.setdefault("brand", load_user_prefs().get("brand", "Laranja"))
s.setdefault("qr_url", load_user_prefs().get("qr_url", ""))
s.setdefault("uploader_key", 0)
s.setdefault("OUTLIER_SIGMA", 3.0)
s.setdefault("TOL_MP", 1.0)
s.setdefault("BATCH_MODE", False)
s.setdefault("_prev_batch", s["BATCH_MODE"])

# =============================================================================
# Segurança / Usuários (PBKDF2 + salt + pepper, lockout, timeout, papéis)
# =============================================================================
PEPPER = os.environ.get("HABI_PEPPER", "")

def _now_iso():
    return datetime.now().isoformat(timespec="seconds")

def _get_db() -> Dict[str, Any]:
    return _load_all_prefs()

def _save_db(db: Dict[str, Any]) -> None:
    _save_all_prefs(db)

def _get_users() -> Dict[str, Any]:
    return _get_db().get("users", {})

def _save_users(users: Dict[str, Any]) -> None:
    db = _get_db()
    db["users"] = users
    _save_db(db)

def _get_security_cfg() -> Dict[str, Any]:
    db = _get_db()
    sec = db.get("security", {})
    sec.setdefault("self_registration", False)
    sec.setdefault("session_timeout_minutes", 30)
    db["security"] = sec
    _save_db(db)
    return sec

def _set_security_cfg(sec: Dict[str, Any]) -> None:
    db = _get_db()
    db["security"] = sec
    _save_db(db)

def _normalize_username(u: str) -> str:
    return (u or "").strip().lower()

def _hash_password(password: str, salt: bytes) -> str:
    material = (password + PEPPER).encode("utf-8")
    dk = hashlib.pbkdf2_hmac("sha256", material, salt, 300_000)
    return dk.hex()

def _create_user_admin_bootstrap(username: str, password: str) -> Tuple[bool, str]:
    users = _get_users()
    if users:
        return False, "Já existem usuários. Use a Administração."
    return _create_user(username, password, roles=["admin"], active=True)

def _create_user(username: str, password: str, roles=None, active=True) -> Tuple[bool, str]:
    username_raw = (username or "").strip()
    username_norm = _normalize_username(username_raw)
    if not (3 <= len(username_norm) <= 32):
        return False, "Usuário deve ter entre 3 e 32 caracteres."
    if not re.fullmatch(r"[a-z0-9_.\-]+", username_norm):
        return False, "Use apenas letras/números/ponto/traço/sublinhado (minúsculo)."
    if len(password or "") < 8:
        return False, "Senha deve ter pelo menos 8 caracteres."
    users = _get_users()
    if username_norm in users:
        return False, "Usuário já existe."
    salt = secrets.token_bytes(16)
    pwd_hash = _hash_password(password, salt)
    users[username_norm] = {
        "username": username_norm,
        "display": username_raw,
        "salt": base64.b64encode(salt).decode("utf-8"),
        "hash": pwd_hash,
        "roles": roles or ["user"],
        "active": bool(active),
        "failed_attempts": 0,
        "lock_until": None,
        "created_at": _now_iso(),
    }
    _save_users(users)
    return True, "Usuário criado com sucesso."

def _set_password(username: str, new_password: str) -> Tuple[bool, str]:
    username_norm = _normalize_username(username)
    users = _get_users()
    u = users.get(username_norm)
    if not u: return False, "Usuário não encontrado."
    if len(new_password or "") < 8:
        return False, "Senha deve ter pelo menos 8 caracteres."
    salt = secrets.token_bytes(16)
    u["salt"] = base64.b64encode(salt).decode("utf-8")
    u["hash"] = _hash_password(new_password, salt)
    users[username_norm] = u
    _save_users(users)
    return True, "Senha alterada."

def _toggle_active(username: str, active: bool) -> None:
    users = _get_users()
    u = users.get(_normalize_username(username))
    if u:
        u["active"] = bool(active)
        _save_users(users)

def _has_role(username: str, role: str) -> bool:
    users = _get_users()
    u = users.get(_normalize_username(username))
    return bool(u and (role in (u.get("roles") or [])))

def _verify_login(username: str, password: str) -> Tuple[bool, str]:
    users = _get_users()
    username_norm = _normalize_username(username)
    u = users.get(username_norm)
    if not u: return False, "Usuário ou senha inválidos."
    # lock?
    if u.get("lock_until"):
        try:
            lock_dt = datetime.fromisoformat(u["lock_until"])
            if datetime.now() < lock_dt:
                return False, "Conta bloqueada temporariamente por tentativas inválidas."
            else:
                u["lock_until"] = None
                u["failed_attempts"] = 0
        except Exception:
            u["lock_until"] = None
            u["failed_attempts"] = 0
    if not u.get("active", True):
        return False, "Conta desativada. Contate o administrador."
    try:
        salt = base64.b64decode(u["salt"])
        given = _hash_password(password or "", salt)
        ok = hmac.compare_digest(given, u["hash"])
        if ok:
            u["failed_attempts"] = 0
            users[username_norm] = u
            _save_users(users)
            return True, "OK"
        # falha
        u["failed_attempts"] = int(u.get("failed_attempts", 0)) + 1
        if u["failed_attempts"] >= 5:
            from datetime import timedelta
            lock_until = datetime.now().replace(microsecond=0) + timedelta(minutes=15)
            u["lock_until"] = lock_until.isoformat()
            msg = "Muitas tentativas inválidas. Conta bloqueada por 15 min."
        else:
            msg = "Usuário ou senha inválidos."
        users[username_norm] = u
        _save_users(users)
        return False, msg
    except Exception:
        return False, "Erro ao validar credenciais."

def _list_users() -> pd.DataFrame:
    users = _get_users()
    rows = []
    for u in users.values():
        rows.append({
            "username": u.get("username"),
            "display": u.get("display") or u.get("username"),
            "roles": ",".join(u.get("roles") or []),
            "active": bool(u.get("active", True)),
            "failed_attempts": int(u.get("failed_attempts", 0)),
            "lock_until": u.get("lock_until"),
            "created_at": u.get("created_at"),
        })
    return pd.DataFrame(rows).sort_values("username") if rows else pd.DataFrame(
        columns=["username","display","roles","active","failed_attempts","lock_until","created_at"]
    )

def _start_session(user: str):
    s["logged_in"] = True
    s["current_user"] = _normalize_username(user)
    s["session_id"] = secrets.token_hex(16)
    s["last_activity"] = datetime.now().timestamp()

def _touch_session():
    timeout_min = int(_get_security_cfg().get("session_timeout_minutes", 30))
    now = datetime.now().timestamp()
    last = float(s.get("last_activity", 0))
    if last and (now - last) > timeout_min * 60:
        s["logged_in"] = False
        s["current_user"] = None
        s["session_id"] = None
        st.warning("Sessão expirada por inatividade.")
        st.rerun()
    s["last_activity"] = now

def _logout():
    s["logged_in"] = False
    s["current_user"] = None
    s["session_id"] = None

# =============================================================================
# Tema/estilo
# =============================================================================
def _apply_query_prefs():
    try:
        qp = st.query_params
        def _first(x): return x[0] if isinstance(x, list) else x
        theme = _first(qp.get("theme") or qp.get("t"))
        brand = _first(qp.get("brand") or qp.get("b"))
        qr    = _first(qp.get("q") or qp.get("qr") or qp.get("u"))
        if theme in ("Escuro moderno", "Claro corporativo"): s["theme_mode"] = theme
        if brand in ("Laranja","Azul","Verde","Roxo"): s["brand"] = brand
        if qr: s["qr_url"] = qr
    except Exception:
        pass
_apply_query_prefs()

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
      --bg:#0b0f19; --panel:#0f172a; --text:#e5e7eb; --muted:#a3a9b7; --line:rgba(148,163,184,.18);
    }}
    .stApp, .main {{ background: var(--bg) !important; color: var(--text) !important; }}
    .block-container{{ padding-top: 12px; max-width: 1300px; }}
    .h-card{{ background: var(--panel); border:1px solid var(--line); border-radius:14px; padding:12px 14px; }}
    .h-kpi-label{{ font-size:12px; color:var(--muted) }}
    .h-kpi{{ font-size:22px; font-weight:800; }}
    .pill{{ display:inline-flex; align-items:center; gap:8px; padding:6px 10px; border-radius:999px;
           border:1px solid var(--line); background:rgba(148,163,184,.10); font-size:12.5px; }}
    .brand-title{{font-weight:800; background:linear-gradient(90deg,var(--brand),var(--brand-700));
                 -webkit-background-clip:text; background-clip:text; color:transparent}}
    .login-card{{max-width:520px;margin:36px auto;background:var(--panel);border:1px solid var(--line);
                 border-radius:16px;padding:16px}}
    .login-title{{font-size:18px;font-weight:800;margin-bottom:8px}}
    </style>
    """
else:
    plt.style.use("default")
    css = f"""
    <style>
    :root {{
      --brand:{brand}; --brand-600:{brand600}; --brand-700:{brand700};
      --bg:#f8fafc; --surface:#ffffff; --text:#0f172a; --muted:#64748b; --line:rgba(2,6,23,.08);
    }}
    .stApp, .main {{ background: var(--bg) !important; color: var(--text) !important; }}
    .block-container{{ padding-top: 12px; max-width: 1300px; }}
    .h-card{{ background: var(--surface); border:1px solid var(--line); border-radius:14px; padding:12px 14px; }}
    .h-kpi-label{{ font-size:12px; color:var(--muted) }}
    .h-kpi{{ font-size:22px; font-weight:800; }}
    .pill{{ display:inline-flex; align-items:center; gap:8px; padding:6px 10px; border-radius:999px;
           border:1px solid var(--line); background:#ffffff; font-size:12.5px; }}
    .brand-title{{font-weight:800; background:linear-gradient(90deg,var(--brand),var(--brand-700));
                 -webkit-background-clip:text; background-clip:text; color:transparent}}
    .login-card{{max-width:520px;margin:36px auto;background:var(--surface);border:1px solid var(--line);
                 border-radius:16px;padding:16px}}
    .login-title{{font-size:18px;font-weight:800;margin-bottom:8px}}
    </style>
    """
st.markdown(css, unsafe_allow_html=True)

# =============================================================================
# Login
# =============================================================================
def show_login() -> None:
    st.markdown(
        "<div class='login-card'><div class='login-title'>🔐 Acesso — Habisolute Analytics</div>"
        "<div class='muted' style='color:var(--muted,#64748b);font-size:13px'>Faça login para continuar.</div>",
        unsafe_allow_html=True
    )
    users_exist = bool(_get_users())

    if not users_exist:
        st.info("Nenhuma conta encontrada. Crie o usuário **administrador** inicial.")
        c1, c2 = st.columns(2)
        with c1:
            admin_user = st.text_input("Usuário (admin)", key="bootstrap_user", placeholder="admin")
        with c2:
            admin_pass = st.text_input("Senha (mín. 8)", key="bootstrap_pass", type="password")
        if st.button("Criar administrador", use_container_width=True, type="primary"):
            ok, msg = _create_user_admin_bootstrap(admin_user or "admin", admin_pass)
            st.success("Administrador criado. Faça login abaixo.") if ok else st.error(msg)
        st.divider()

    c1, c2 = st.columns([1,1])
    with c1:
        user = st.text_input("Usuário", key="login_user", placeholder="seu.usuario")
    with c2:
        pwd = st.text_input("Senha", key="login_pass", type="password", placeholder="••••••••")
    if st.button("Acessar", use_container_width=True, type="primary"):
        ok, msg = _verify_login(user, pwd)
        if ok:
            _start_session(user)
            up = load_user_prefs(user_key=s["current_user"])
            s["theme_mode"] = up.get("theme_mode", s.get("theme_mode"))
            s["brand"]      = up.get("brand", s.get("brand"))
            s["qr_url"]     = up.get("qr_url", s.get("qr_url"))
            st.rerun()
        else:
            st.error(msg)
    st.markdown("</div>", unsafe_allow_html=True)

# Primeira barreira
if not s["logged_in"]:
    show_login()
    st.stop()
else:
    _touch_session()
    # -------------------- Barra de preferências --------------------
st.markdown("""
<style>
  .prefs-bar { margin-top: 28px; }
  @media (min-width: 1100px) { .prefs-bar { margin-top: 36px; } }
</style>
""", unsafe_allow_html=True)

with st.container():
    st.markdown("<div class='prefs-bar'>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([1.1, 1.1, 2.5, 1.1])

    with c1:
        s["theme_mode"] = st.radio(
            "Tema", ["Escuro moderno", "Claro corporativo"],
            index=0 if s.get("theme_mode") == "Escuro moderno" else 1,
            horizontal=True
        )
    with c2:
        s["brand"] = st.selectbox("🎨 Cor da marca", ["Laranja","Azul","Verde","Roxo"],
                                  index=["Laranja","Azul","Verde","Roxo"].index(s.get("brand","Laranja")))
    with c3:
        s["qr_url"] = st.text_input("URL do resumo (QR opcional na capa do PDF)",
                                    value=s.get("qr_url",""), placeholder="https://exemplo.com/resumo")
    with c4:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("💾 Salvar como padrão", use_container_width=True, key="k_save"):
                save_user_prefs({
                    "theme_mode": s["theme_mode"],
                    "brand":      s["brand"],
                    "qr_url":     s["qr_url"],
                }, user_key=s.get("current_user") or "default")
                try:
                    qp = st.query_params
                    qp.update({"theme": s["theme_mode"], "brand": s["brand"], "q": s["qr_url"]})
                except Exception:
                    pass
                st.success("Preferências salvas!")
        with col_b:
            if st.button("Sair", use_container_width=True, key="k_logout"):
                _logout(); st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# =============================================================================
# Administração → Usuários (somente admin)
# =============================================================================
if _has_role(s.get("current_user",""), "admin"):
    with st.expander("🔧 Administração — Usuários", expanded=False):
        sec = _get_security_cfg()
        c1, c2 = st.columns([1,1])
        with c1:
            sec["self_registration"] = st.toggle(
                "Permitir auto-cadastro público (desaconselhado)", value=bool(sec.get("self_registration", False))
            )
        with c2:
            sec["session_timeout_minutes"] = st.number_input(
                "Timeout de sessão (minutos)", min_value=5, max_value=240, step=5,
                value=int(sec.get("session_timeout_minutes", 30))
            )
        if st.button("Salvar políticas de segurança", use_container_width=True):
            _set_security_cfg(sec); st.success("Políticas salvas.")

        st.divider()
        st.subheader("👤 Gerenciar contas")
        df_users = _list_users()
        st.dataframe(df_users, use_container_width=True)

        st.markdown("### Criar usuário")
        u1, u2, u3 = st.columns([1,1,1])
        with u1:
            nu = st.text_input("Usuário (minúsculo)", key="newuser_u", placeholder="nome.sobrenome")
        with u2:
            np = st.text_input("Senha", key="newuser_p", type="password")
        with u3:
            roles_opt = st.multiselect("Papéis", ["admin","user"], default=["user"], key="newuser_roles")
        if st.button("Adicionar usuário", use_container_width=True):
            ok, msg = _create_user(nu, np, roles=roles_opt or ["user"], active=True)
            st.success(msg) if ok else st.error(msg)

        st.markdown("### Ações rápidas")
        a1, a2, a3 = st.columns(3)
        with a1:
            tgt = st.text_input("Usuário alvo", key="act_user")
        with a2:
            newp = st.text_input("Nova senha", key="act_pass", type="password")
        with a3:
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            if st.button("Trocar senha", use_container_width=True):
                ok, msg = _set_password(tgt, newp)
                st.success(msg) if ok else st.error(msg)

        b1, b2 = st.columns(2)
        with b1:
            tgt2 = st.text_input("Usuário p/ ativar/desativar", key="act_user2")
        with b2:
            act = st.selectbox("Estado", ["Ativar","Desativar"], index=0)
            if st.button("Aplicar", use_container_width=True):
                _toggle_active(tgt2, active=(act=="Ativar"))
                st.success("Estado atualizado.")

# =============================================================================
# Sidebar
# =============================================================================
with st.sidebar:
    st.markdown("### ⚙️ Opções do relatório")
    s["BATCH_MODE"] = st.toggle("Modo Lote (vários PDFs)", value=bool(s["BATCH_MODE"]))
    if s["BATCH_MODE"] != s["_prev_batch"]:
        s["_prev_batch"] = s["BATCH_MODE"]; s["uploader_key"] += 1
    s["TOL_MP"] = st.slider("Tolerância Real × Estimado (MPa)", 0.0, 5.0, float(s["TOL_MP"]), 0.1)
    st.markdown("---")
    st.caption(f"Logado como: {s.get('current_user') or '—'}")

TOL_MP = float(s["TOL_MP"])
BATCH_MODE = bool(s["BATCH_MODE"])

# =============================================================================
# Utilidades de parsing (idênticas/baseadas no seu app)
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
            if m:
                return _limpa_usina_extra(m.group(1)) or _limpa_usina_extra(m.group(0))
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
        m_nf = re.search(r"(?i)abat(?:imento|\.?im\.?)\s*(?:de\s*)?nf[^0-9]*" r"(\d+(?:\.\d+)?)(?:\s*\+?-?\s*\d+(?:\.\d+)?)?\s*mm?", s_clean)
        if m_nf and abat_nf is None:
            try: abat_nf = float(m_nf.group(1))
            except Exception: pass
        m_obra = re.search(r"(?i)abat(?:imento|\.?im\.?).*(obra|medido em obra)[^0-9]*" r"(\d+(?:\.\d+)?)\s*mm", s_clean)
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
    age_tokens = {3,7,14,21,28,56,63,90}
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
            try:
                val = float(num)
            except ValueError:
                continue
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
                    try:
                        fck_projeto = float(valores_fck[0])
                    except Exception:
                        pass

    usina_nome = _limpa_usina_extra(_detecta_usina(linhas_todas))
    abat_nf_pdf, abat_obra_pdf = _detecta_abatimentos(linhas_todas)

    dados = []
    relatorio_cabecalho = None

    for sline in linhas_todas:
        partes = sline.split()

        if sline.startswith("Relatório:"):
            m_rel = re.search(r"Relatório:\s*(\d+)", sline)
            if m_rel:
                relatorio_cabecalho = m_rel.group(1)
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
                            idade = v
                            idade_idx = j
                            break

                resistencia, res_idx = None, None
                if idade_idx is not None:
                    for j in range(idade_idx + 1, len(partes)):
                        t = partes[j]
                        if float_token.match(t):
                            resistencia = float(t.replace(",", "."))
                            res_idx = j
                            break
                if idade is None or resistencia is None:
                    continue

                nf, nf_idx = None, None
                start_nf = (res_idx + 1) if res_idx is not None else (idade_idx + 1)
                for j in range(start_nf, len(partes)):
                    tok = partes[j]
                    if nf_regex.match(tok) and tok != cp:
                        nf = tok
                        nf_idx = j
                        break

                abat_obra_val = None
                if i_data is not None:
                    for j in range(i_data - 1, max(-1, i_data - 6), -1):
                        tok = partes[j]
                        if re.fullmatch(r"\d{2,3}", tok):
                            v = int(tok)
                            if 20 <= v <= 250:
                                abat_obra_val = float(v)
                                break

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
                try:
                    val_f = float(valor)
                except Exception:
                    continue
                if val_f not in uniques:
                    uniques.append(val_f)
            if uniques:
                rel_map[rel] = uniques[0]

        fallback_fck = None
        if isinstance(fck_projeto, (int, float)):
            fallback_fck = float(fck_projeto)
        else:
            candidatos = []
            for valores in fck_por_relatorio.values():
                candidatos.extend(valores)
            candidatos.extend(fck_valores_globais)
            for cand in candidatos:
                try:
                    fallback_fck = float(cand)
                    break
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
# KPIs e utilidades gráficas
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
        score = (float(p28 or 0)*0.6) + (float(p63 or 0)*0.4)
        if score >= 90: return ("✅ Bom", "#16a34a")
        if score >= 75: return ("⚠️ Atenção", "#d97706")
        return ("🔴 Crítico", "#ef4444")

    status_txt, status_cor = _semaforo(pct28, pct63)
    return {"pct28":pct28,"pct63":pct63,"media":media_geral,"dp":dp_geral,"n_rel":n_rel,"status_txt":status_txt,"status_cor":status_cor}

def place_right_legend(ax):
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), loc="upper left", bbox_to_anchor=(1.02,1.0),
              frameon=False, ncol=1, handlelength=2.2, handletextpad=0.8, labelspacing=0.35, prop={"size":9})
    plt.subplots_adjust(right=0.80)

def _img_from_fig(_fig, w=620, h=420):  # >>> gráficos MAIORES para o PDF
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    _fig.savefig(tmp.name, dpi=220, bbox_inches="tight")
    return RLImage(tmp.name, width=w, height=h)

def _abat_nf_label(df_: pd.DataFrame) -> str:
    snf = pd.to_numeric(df_.get("Abatimento NF (mm)"), errors="coerce").dropna()
    stol = pd.to_numeric(df_.get("Abatimento NF tol (mm)"), errors="coerce").dropna()
    if snf.empty: return "—"
    v = float(snf.mode().iloc[0])
    t = float(stol.mode().iloc[0]) if not stol.empty else 0.0
    return f"{v:.0f} ± {t:.0f} mm"

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
    use_landscape = (len(df.columns) >= 8)
    pagesize = landscape(A4) if use_landscape else A4

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        leftMargin=18, rightMargin=18,
        topMargin=34, bottomMargin=54
    )

    styles = getSampleStyleSheet()
    styles["Title"].fontName = "Helvetica-Bold";  styles["Title"].fontSize = 18
    styles["Heading2"].fontName = "Helvetica-Bold"; styles["Heading2"].fontSize = 14
    styles["Heading3"].fontName = "Helvetica-Bold"; styles["Heading3"].fontSize = 12
    styles["Normal"].fontName = "Helvetica";       styles["Normal"].fontSize = 9

    story = []
    # ===== Cabeçalho completo =====
    story.append(Paragraph("<b>Habisolute Engenharia e Controle Tecnológico</b>", styles['Title']))
    story.append(Paragraph("Relatório de Rompimento de Corpos de Prova", styles['Heading2']))
    if s.get("qr_url"):
        story.append(Paragraph(f"<b>Resumo/QR:</b> {s['qr_url']}", styles['Normal']))
    story.append(Paragraph(f"<b>Obra:</b> {obra_label}", styles['Normal']))
    story.append(Paragraph(f"<b>Data do relatório:</b> {data_label}", styles['Normal']))  # << corrigido
    story.append(Paragraph(f"<b>fck de projeto:</b> {fck_label} MPa", styles['Normal']))
    story.append(Paragraph(f"<b>Abatimento de NF:</b> {_abat_nf_label(df)}", styles['Normal']))
    story.append(Spacer(1, 8))

    # ===== Tabela principal =====
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
    story.append(table)
    story.append(Spacer(1, 10))

    # ===== Resumo estatístico =====
    if not stats.empty:
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

    # ===== Gráficos (MAIORES no PDF) =====
    if fig1: story.append(_img_from_fig(fig1)); story.append(Spacer(1, 8))
    if fig2: story.append(_img_from_fig(fig2)); story.append(Spacer(1, 8))
    if fig3: story.append(_img_from_fig(fig3)); story.append(Spacer(1, 8))
    if fig4: story.append(_img_from_fig(fig4)); story.append(Spacer(1, 8))

    # ===== Verificação do fck =====
    if verif_fck_df is not None and not verif_fck_df.empty:
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

    # ===== Condição Real × Estimado =====
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
        story.append(tc); story.append(Spacer(1, 10))

    # ===== Verificação detalhada por CP (completa) =====
    if pv_detalhe is not None and not pv_detalhe.empty:
        story.append(PageBreak())
        story.append(Paragraph("Verificação detalhada por CP (7/28/63 dias)", styles["Heading3"]))
        head = list(pv_detalhe.columns)
        rows_p = pv_detalhe.values.tolist()
        tp = Table([head] + rows_p, repeatRows=1)
        tp.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
            ("GRID",(0,0),(-1,-1),0.5,colors.black),
            ("ALIGN",(0,0),(-1,-1),"CENTER"),
            ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
            ("FONTSIZE",(0,0),(-1,-1),8.3),
        ]))
        story.append(tp)

    # Canvas com rodapé/numeração
    doc.build(story, canvasmaker=NumberedCanvas)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf
    # =============================================================================
# Cabeçalho e uploader
# =============================================================================
st.markdown("<h3 class='brand-title'>🏗️ Habisolute IA 🤖</h3>", unsafe_allow_html=True)
st.caption("Envie certificados em PDF e gere análises, gráficos, KPIs e relatório final com capa personalizada.")

up_help = "Carregue 1 PDF (ou vários em modo lote)."
_uploader_key = f"uploader_{'multi' if BATCH_MODE else 'single'}_{s['uploader_key']}"

if BATCH_MODE:
    uploaded_files = st.file_uploader("📁 PDF(s)", type=["pdf"], accept_multiple_files=True, key=_uploader_key, help=up_help)
else:
    up1 = st.file_uploader("📁 PDF (1 arquivo)", type=["pdf"], accept_multiple_files=False, key=_uploader_key, help=up_help)
    uploaded_files = [up1] if up1 is not None else []

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

    if not frames:
        st.error("⚠️ Não encontrei CPs válidos nos PDFs enviados.")
    else:
        df = pd.concat(frames, ignore_index=True)

        # ---------------- Filtros
        st.markdown("#### Filtros")
        fc1, fc2, fc3 = st.columns([2.0, 2.0, 1.0])
        with fc1:
            rels = sorted(df["Relatório"].astype(str).unique())
            sel_rels = st.multiselect("Relatórios", rels, default=rels)

        def to_date(d):
            try: return datetime.strptime(str(d), "%d/%m/%Y").date()
            except Exception: return None

        df["_DataObj"] = df["Data Certificado"].apply(to_date)
        valid_dates = [d for d in df["_DataObj"] if d is not None]
        with fc2:
            if valid_dates:
                dmin, dmax = min(valid_dates), max(valid_dates)
                dini, dfim = st.date_input("Intervalo de data do certificado", (dmin, dmax))
            else:
                dini, dfim = None, None
        with fc3:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("🔄 Limpar filtros / Novo upload", use_container_width=True):
                s["uploader_key"] += 1; st.rerun()

        mask = df["Relatório"].astype(str).isin(sel_rels)
        if valid_dates and dini and dfim:
            mask = mask & df["_DataObj"].apply(lambda d: d is not None and dini <= d <= dfim)
        df_view = df.loc[mask].drop(columns=["_DataObj"]).copy()

        df_view["_FckLabel"] = df_view["Fck Projeto"].apply(_normalize_fck_label)
        fck_labels = list(dict.fromkeys(df_view["_FckLabel"]))
        multiple_fck_detected = len(fck_labels) > 1
        if multiple_fck_detected:
            st.warning("Detectamos múltiplos fck no conjunto selecionado. Escolha qual deseja analisar.")
            selected_fck_label = st.selectbox("fck para análise", fck_labels, format_func=lambda lbl: lbl if lbl != "—" else "Não informado")
            df_view = df_view[df_view["_FckLabel"] == selected_fck_label].copy()
        else:
            selected_fck_label = fck_labels[0] if fck_labels else "—"
        if df_view.empty:
            st.info("Nenhum dado disponível para o fck selecionado."); st.stop()
        df_view = df_view.drop(columns=["_FckLabel"], errors="ignore")

        stats_cp_idade = (
            df_view.groupby(["CP", "Idade (dias)"])["Resistência (MPa)"]
                   .agg(Média="mean", Desvio_Padrão="std", n="count").reset_index()
        )

        # ---------------- Visão Geral + KPIs
        st.markdown("#### Visão Geral")
        obra_label, data_label, fck_label = "—", "—", selected_fck_label or "—"
        if not df_view.empty:
            ob = sorted(set(df_view["Obra"].astype(str)))
            obra_label = ob[0] if len(ob) == 1 else f"Múltiplas ({len(ob)})"
            fck_candidates: List[str] = []
            for raw in df_view["Fck Projeto"].tolist():
                normalized = _to_float_or_none(raw)
                if normalized is not None:
                    formatted = _format_float_label(normalized)
                    if formatted != "—": fck_candidates.append(formatted)
                else:
                    raw_str = str(raw).strip()
                    if raw_str and raw_str.lower() != "nan": fck_candidates.append(raw_str)
            if fck_candidates: fck_label = ", ".join(dict.fromkeys(fck_candidates))
            datas_validas = [to_date(x) for x in df_view["Data Certificado"].unique()]
            datas_validas = [d for d in datas_validas if d is not None]
            if datas_validas:
                di, df_ = min(datas_validas), max(datas_validas)
                data_label = di.strftime('%d/%m/%Y') if di == df_ else f"{di.strftime('%d/%m/%Y')} — {df_.strftime('%d/%m/%Y')}"

        def fmt_pct(v): return "--" if v is None else f"{v:.0f}%"
        fck_series_all = pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
        fck_val = float(fck_series_all.mode().iloc[0]) if not fck_series_all.empty else None
        KPIs = compute_exec_kpis(df_view, fck_val)

        k1, k2, k3, k4, k5, k6 = st.columns(6)
        with k1:
            st.markdown(f'<div class="h-card"><div class="h-kpi-label">Obra</div><div class="h-kpi">{obra_label}</div></div>', unsafe_allow_html=True)
        with k2:
            st.markdown(f'<div class="h-card"><div class="h-kpi-label">Data da moldagem</div><div class="h-kpi">{data_label}</div></div>', unsafe_allow_html=True)
        with k3:
            st.markdown(f'<div class="h-card"><div class="h-kpi-label">fck de projeto (MPa)</div><div class="h-kpi">{fck_label}</div></div>', unsafe_allow_html=True)
        with k4:
            st.markdown(f'<div class="h-card"><div class="h-kpi-label">Tolerância aplicada (MPa)</div><div class="h-kpi">±{TOL_MP:.1f}</div></div>', unsafe_allow_html=True)
        with k5:
            st.markdown(f'<div class="h-card"><div class="h-kpi-label">CPs com fck 28d</div><div class="h-kpi">{fmt_pct(KPIs["pct28"])}</div></div>', unsafe_allow_html=True)
        with k6:
            st.markdown(f'<div class="h-card"><div class="h-kpi-label">CPs com fck 63d</div><div class="h-kpi">{fmt_pct(KPIs["pct63"])}</div></div>', unsafe_allow_html=True)

        e1, e2, e3, e4 = st.columns(4)
        with e1:
            media_txt = "--" if KPIs["media"] is None else f"{KPIs['media']:.1f} MPa"
            st.markdown(f'<div class="h-card"><div class="h-kpi-label">Média geral</div><div class="h-kpi">{media_txt}</div></div>', unsafe_allow_html=True)
        with e2:
            dp_txt = "--" if KPIs["dp"] is None else f"{KPIs['dp']:.1f}"
            st.markdown(f'<div class="h-card"><div class="h-kpi-label">Desvio-padrão</div><div class="h-kpi">{dp_txt}</div></div>', unsafe_allow_html=True)
        with e3:
            st.markdown(f'<div class="h-card"><div class="h-kpi-label">Relatórios</div><div class="h-kpi">{KPIs["n_rel"]}</div></div>', unsafe_allow_html=True)
        with e4:
            snf = pd.to_numeric(df_view.get("Abatimento NF (mm)"), errors="coerce")
            stol = pd.to_numeric(df_view.get("Abatimento NF tol (mm)"), errors="coerce") if "Abatimento NF tol (mm)" in df_view.columns else pd.Series(dtype=float)
            abat_nf_label = "—"
            if snf is not None and not snf.dropna().empty:
                v = float(snf.dropna().mode().iloc[0])
                if stol is not None and not stol.dropna().empty:
                    t = float(stol.dropna().mode().iloc[0]); abat_nf_label = f"{v:.0f} ± {t:.0f} mm"
                else:
                    abat_nf_label = f"{v:.0f} mm"
            st.markdown(f'<div class="h-card"><div class="h-kpi-label">Abatimento NF</div><div class="h-kpi">{abat_nf_label}</div></div>', unsafe_allow_html=True)

        if multiple_fck_detected:
            st.markdown("<div class='pill' style='background:rgba(250,204,21,.16); color:#b45309; font-weight:700;'>⚠️ FCK Análise = Atenção — Certificado com 02 Fck</div>", unsafe_allow_html=True)

        # Semáforo + explicação
        p28 = KPIs.get("pct28"); p63 = KPIs.get("pct63")
        score = (0 if p28 is None else 0.6 * p28) + (0 if p63 is None else 0.4 * p63)
        def _hits(df_src, age, fck):
            if fck is None or pd.isna(fck): return (0,0)
            sub = df_src[df_src["Idade (dias)"] == age].groupby("CP")["Resistência (MPa)"].mean()
            return int((sub >= fck).sum()), int(sub.shape[0])
        h28, t28 = _hits(df_view, 28, fck_val); h63, t63 = _hits(df_view, 63, fck_val)
        st.markdown(f"<div class='pill' style='margin:8px 0 2px 0; color:{KPIs['status_cor']}; font-weight:800'>{KPIs['status_txt']}</div>", unsafe_allow_html=True)

        # ---------------- Tabelas base
        st.write("#### Resultados Individuais"); st.dataframe(df_view, use_container_width=True)
        st.write("#### Estatísticas por CP"); st.dataframe(stats_cp_idade, use_container_width=True)

        # ---------------- Gráficos
        st.markdown("---"); st.markdown("### Gráficos")
        st.sidebar.subheader("🎯 Foco nos gráficos")
        cp_foco_manual = st.sidebar.text_input("Digitar CP p/ gráficos (opcional)", "", key="cp_manual")
        cp_select = st.sidebar.selectbox("CP para gráficos", ["(Todos)"] + sorted(df_view["CP"].astype(str).unique()), key="cp_select")
        cp_focus = (cp_foco_manual.strip() or (cp_select if cp_select != "(Todos)" else "")).strip()
        df_plot = df_view[df_view["CP"].astype(str) == cp_focus].copy() if cp_focus else df_view.copy()

        # fck ativo
        fck_series_focus = pd.to_numeric(df_plot["Fck Projeto"], errors="coerce").dropna()
        fck_series_all_g = pd.to_numeric(df_view["Fck Projeto"], errors="coerce").dropna()
        fck_active = float(fck_series_focus.mode().iloc[0]) if not fck_series_focus.empty else (float(fck_series_all_g.mode().iloc[0]) if not fck_series_all_g.empty else None)

        # Estatística geral por idade (para médias)
        stats_all_focus = df_plot.groupby("Idade (dias)")["Resistência (MPa)"].agg(mean="mean", std="std", count="count").reset_index()

        # Gráfico 1 — Real
        st.write("##### Gráfico 1 — Crescimento da Resistência (Real)")
        fig1, ax = plt.subplots(figsize=(9.8, 5.1))
        for cp, sub in df_plot.groupby("CP"):
            sub = sub.sort_values("Idade (dias)")
            ax.plot(sub["Idade (dias)"], sub["Resistência (MPa)"], marker="o", linewidth=1.6, label=f"CP {cp}")
        sa_dp = stats_all_focus[stats_all_focus["count"] >= 2].copy()
        if not sa_dp.empty:
            ax.plot(sa_dp["Idade (dias)"], sa_dp["mean"], linewidth=2.2, marker="s", label="Média")
            _sdp = sa_dp.dropna(subset=["std"]).copy()
            if not _sdp.empty:
                ax.fill_between(_sdp["Idade (dias)"], _sdp["mean"] - _sdp["std"], _sdp["mean"] + _sdp["std"], alpha=0.2, label="±1 DP")
        if fck_active is not None: ax.axhline(fck_active, linestyle=":", linewidth=2, label=f"fck projeto ({fck_active:.1f} MPa)")
        ax.set_xlabel("Idade (dias)"); ax.set_ylabel("Resistência (MPa)")
        ax.set_title("Crescimento da resistência por corpo de prova")
        place_right_legend(ax); ax.grid(True, linestyle="--", alpha=0.35)
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        st.pyplot(fig1)

        _buf1 = io.BytesIO(); fig1.savefig(_buf1, format="png", dpi=200, bbox_inches="tight")
        st.download_button("🖼️ Baixar Gráfico 1 (PNG)", data=_buf1.getvalue(), file_name="grafico1_real.png", mime="image/png")

        # Gráfico 2 — Estimado
        st.write("##### Gráfico 2 — Curva Estimada (Referência técnica)")
        fig2, est_df = None, None
        fck28 = df_plot.loc[df_plot["Idade (dias)"] == 28, "Resistência (MPa)"].mean()
        fck7  = df_plot.loc[df_plot["Idade (dias)"] == 7,  "Resistência (MPa)"].mean()
        if pd.notna(fck28):
            est_df = pd.DataFrame({"Idade (dias)": [7, 28, 63], "Resistência (MPa)": [fck28*0.65, fck28, fck28*1.15]})
        elif pd.notna(fck7):
            _f28 = fck7 / 0.70
            est_df = pd.DataFrame({"Idade (dias)": [7, 28, 63], "Resistência (MPa)": [float(fck7), float(_f28), float(_f28)*1.15]})
        if est_df is not None:
            fig2, ax2 = plt.subplots(figsize=(8.4, 4.9))
            ax2.plot(est_df["Idade (dias)"], est_df["Resistência (MPa)"], linestyle="--", marker="o", linewidth=2, label="Curva Estimada")
            for x, y in zip(est_df["Idade (dias)"], est_df["Resistência (MPa)"]):
                ax2.text(x, y, f"{y:.1f}", ha="center", va="bottom", fontsize=9)
            ax2.set_title("Curva estimada (referência técnica, não critério normativo)")
            ax2.set_xlabel("Idade (dias)"); ax2.set_ylabel("Resistência (MPa)")
            place_right_legend(ax2); ax2.grid(True, linestyle="--", alpha=0.5)
            st.pyplot(fig2)
            _buf2 = io.BytesIO(); fig2.savefig(_buf2, format="png", dpi=200, bbox_inches="tight")
            st.download_button("🖼️ Baixar Gráfico 2 (PNG)", data=_buf2.getvalue(), file_name="grafico2_estimado.png", mime="image/png")
        else:
            st.info("Não foi possível calcular a curva estimada (sem médias em 7 ou 28 dias).")

        # Gráfico 3 — Comparação
        st.write("##### Gráfico 3 — Comparação Real × Estimado (médias)")
        fig3, cond_df, verif_fck_df = None, None, None
        mean_by_age = df_plot.groupby("Idade (dias)")["Resistência (MPa)"].mean()
        m7  = mean_by_age.get(7,  float("nan"))
        m28 = mean_by_age.get(28, float("nan"))
        m63 = mean_by_age.get(63, float("nan"))
        verif_fck_df = pd.DataFrame({
            "Idade (dias)": [7, 28, 63],
            "Média Real (MPa)": [m7, m28, m63],
            "fck Projeto (MPa)": [float("nan"), (fck_active if fck_active is not None else float("nan")),(fck_active if fck_active is not None else float("nan"))],
        })
        if est_df is not None:
            sa = stats_all_focus.copy(); sa["std"] = sa["std"].fillna(0.0)
            fig3, ax3 = plt.subplots(figsize=(9.8, 5.1))
            ax3.plot(sa["Idade (dias)"], sa["mean"], marker="s", linewidth=2, label=("Média (CP focado)" if cp_focus else "Média Real"))
            _sa_dp = sa[sa["count"] >= 2]
            if not _sa_dp.empty:
                ax3.fill_between(_sa_dp["Idade (dias)"], _sa_dp["mean"] - _sa_dp["std"], _sa_dp["mean"] + _sa_dp["std"], alpha=0.2, label="Real ±1 DP")
            ax3.plot(est_df["Idade (dias)"], est_df["Resistência (MPa)"], linestyle="--", marker="o", linewidth=2, label="Estimado")
            if fck_active is not None: ax3.axhline(fck_active, linestyle=":", linewidth=2, label=f"fck projeto ({fck_active:.1f} MPa)")
            ax3.set_xlabel("Idade (dias)"); ax3.set_ylabel("Resistência (MPa)")
            ax3.set_title("Comparação Real × Estimado (médias)")
            place_right_legend(ax3); ax3.grid(True, linestyle="--", alpha=0.5)
            st.pyplot(fig3)
            _buf3 = io.BytesIO(); fig3.savefig(_buf3, format="png", dpi=200, bbox_inches="tight")
            st.download_button("🖼️ Baixar Gráfico 3 (PNG)", data=_buf3.getvalue(), file_name="grafico3_comparacao.png", mime="image/png")

            def _status_row(delta, tol):
                if pd.isna(delta): return "⚪ Sem dados"
                if abs(delta) <= tol: return "✅ Dentro dos padrões"
                return "🔵 Acima do padrão" if delta > 0 else "🔴 Abaixo do padrão"
            _TOL = float(TOL_MP)
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
            st.write("#### 📊 Condição Real × Estimado (médias)"); st.dataframe(cond_df, use_container_width=True)
        else:
            st.info("Sem curva estimada → não é possível comparar médias (Gráfico 3).")

        # Gráfico 4 — Pareamento ponto-a-ponto
        st.write("##### Gráfico 4 — Real × Estimado ponto-a-ponto (sem médias)")
        fig4, pareamento_df = None, None
        if est_df is not None and not est_df.empty:
            est_map = dict(zip(est_df["Idade (dias)"], est_df["Resistência (MPa)"]))
            pares = []
            for cp, sub in df_plot.groupby("CP"):
                for _, r in sub.iterrows():
                    idade = int(r["Idade (dias)"])
                    if idade in est_map:
                        real = float(r["Resistência (MPa)"]); est  = float(est_map[idade])
                        delta = real - est; _TOL = float(TOL_MP)
                        status = "✅ OK" if abs(delta) <= _TOL else ("🔵 Acima" if delta > 0 else "🔴 Abaixo")
                        pares.append([str(cp), idade, real, est, delta, status])
            pareamento_df = pd.DataFrame(pares, columns=["CP","Idade (dias)","Real (MPa)","Estimado (MPa)","Δ","Status"]).sort_values(["CP","Idade (dias)"])
            fig4, ax4 = plt.subplots(figsize=(10.4, 5.2))
            for cp, sub in df_plot.groupby("CP"):
                sub = sub.sort_values("Idade (dias)")
                x = sub["Idade (dias)"].tolist()
                y_real = sub["Resistência (MPa)"].tolist()
                x_est = [i for i in x if i in est_map]
                y_est = [est_map[i] for i in x_est]
                ax4.plot(x, y_real, marker="o", linewidth=1.6, label=f"CP {cp} — Real")
                if x_est:
                    ax4.plot(x_est, y_est, marker="^", linestyle="--", linewidth=1.6, label=f"CP {cp} — Est.")
                    for xx, yr, ye in zip(x_est, [rv for i, rv in zip(x, y_real) if i in est_map], y_est):
                        ax4.vlines(xx, min(yr, ye), max(yr, ye), linestyles=":", linewidth=1)
            if fck_active is not None: ax4.axhline(fck_active, linestyle=":", linewidth=2, label=f"fck projeto ({fck_active:.1f} MPa)")
            ax4.set_xlabel("Idade (dias)"); ax4.set_ylabel("Resistência (MPa)")
            ax4.set_title("Pareamento Real × Estimado por CP (sem médias)")
            place_right_legend(ax4); ax4.grid(True, linestyle="--", alpha=0.5); st.pyplot(fig4)
            _buf4 = io.BytesIO(); fig4.savefig(_buf4, format="png", dpi=200, bbox_inches="tight")
            st.download_button("🖼️ Baixar Gráfico 4 (PNG)", data=_buf4.getvalue(), file_name="grafico4_pareamento.png", mime="image/png")
            st.write("#### 📑 Pareamento ponto-a-ponto"); st.dataframe(pareamento_df, use_container_width=True)
        else:
            st.info("Sem curva estimada → não é possível parear os pontos (Gráfico 4).")

        # ===== Verificação do fck — RESUMO
        st.write("#### ✅ Verificação do fck de Projeto")
        def _badge(txt, color="#e5e7eb"): return f"<span class='pill' style='color:{color}; font-weight:700'>{txt}</span>"
        linhas = []
        if pd.notna(m7): linhas.append(_badge(f"7 dias • média {m7:.2f} MPa", color="#f59e0b"))
        else: linhas.append(_badge("7 dias • sem dados", color="#f59e0b"))
        if fck_active is None:
            linhas.append(_badge("28 dias • fck não identificado", color="#9ca3af"))
            linhas.append(_badge("63 dias • fck não identificado", color="#9ca3af"))
        else:
            if pd.isna(m28): linhas.append(_badge("28 dias • sem dados", color="#9ca3af"))
            else:
                ok28 = m28 >= fck_active
                linhas.append(_badge(f"28 dias • {'atingiu' if ok28 else 'não atingiu'} fck ({m28:.2f} {'≥' if ok28 else '<'} {fck_active:.2f} MPa)", color=("#16a34a" if ok28 else "#ef4444")))
            if pd.isna(m63): linhas.append(_badge("63 dias • sem dados", color="#9ca3af"))
            else:
                ok63 = m63 >= fck_active
                linhas.append(_badge(f"63 dias • {'atingiu' if ok63 else 'não atingiu'} fck ({m63:.2f} {'≥' if ok63 else '<'} {fck_active:.2f} MPa)", color=("#16a34a" if ok63 else "#ef4444")))
        st.markdown("<div style='display:flex;flex-wrap:wrap;gap:10px'>"+ "".join(linhas) +"</div>", unsafe_allow_html=True)

        verif_fck_df = pd.DataFrame({
            "Idade (dias)": [7, 28, 63],
            "Média Real (MPa)": [m7 if pd.notna(m7) else float("nan"), m28 if pd.notna(m28) else float("nan"), m63 if pd.notna(m63) else float("nan")],
            "fck Projeto (MPa)": [float("nan"), (fck_active if fck_active is not None else float("nan")),(fck_active if fck_active is not None else float("nan"))],
        })
        resumo_status = []
        for idade, media, fckp in verif_fck_df.itertuples(index=False):
            if idade == 7: resumo_status.append("🟡 Informativo (7d)")
            else:
                if pd.isna(media) or pd.isna(fckp): resumo_status.append("⚪ Sem dados")
                else: resumo_status.append("🟢 Atingiu fck" if media >= fckp else "🔴 Não atingiu fck")
        verif_fck_df["Status"] = resumo_status
        st.dataframe(verif_fck_df, use_container_width=True)

        # ===== Verificação detalhada por CP (7/28/63) — inclui réplicas, status lado a lado
        st.markdown("#### ✅ Verificação detalhada por CP (7/28/63 dias)")
        tmp_v = df_view[df_view["Idade (dias)"].isin([7, 28, 63])].copy()
        pv = pd.DataFrame()
        if not tmp_v.empty:
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
            def _flat(age, rep): return f"{age}d (MPa)" if rep == 1 else f"{age}d #{rep} (MPa)"
            pv = pv_multi.copy(); pv.columns = [_flat(a, r) for (a, r) in pv_multi.columns]; pv = pv.reset_index()
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
                ok28 = pd.Series([None]*pv_multi.shape[0], index=pv_multi.index)
            def _status_from_ok(ok):
                if ok is None: return "⚪ Sem dados"
                return "🟢 Atingiu fck" if ok else "🔴 Não atingiu fck"
            status_df = pd.DataFrame({
                "Status 7d":  [ _status_text_media(v, 7,  fck_active2) for v in media_7.reindex(pv_multi.index) ],
                "Status 28d": [ _status_from_ok(v) for v in ok28.reindex(pv_multi.index) ],
                "Status 63d": [ _status_text_media(v, 63, fck_active2) for v in media_63.reindex(pv_multi.index) ],
            }, index=pv_multi.index)
            pv = pv.merge(status_df, left_on="CP", right_index=True, how="left")
            cols_cp = ["CP"]; cols_7 = [c for c in pv.columns if c.startswith("7d")]; cols_28 = [c for c in pv.columns if c.startswith("28d")]; cols_63 = [c for c in pv.columns if c.startswith("63d")]
            ordered_cols = cols_cp + cols_7 + (["Status 7d"] if "Status 7d" in pv.columns else []) + cols_28 + (["Status 28d"] if "Status 28d" in pv.columns else []) + cols_63 + (["Status 63d"] if "Status 63d" in pv.columns else [])
            pv = pv.rename(columns={"Status 7d":"7 dias — Status","Status 28d":"28 dias — Status","Status 63d":"63 dias — Status"})
            ordered_cols = ["7 dias — Status" if c=="Status 7d" else "28 dias — Status" if c=="Status 28d" else "63 dias — Status" if c=="Status 63d" else c for c in ordered_cols]
            pv = pv[[c for c in ordered_cols if c in pv.columns]]
            st.dataframe(pv, use_container_width=True)
        else:
            st.info("Sem CPs de 7/28/63 dias no filtro atual.")
            pv = pd.DataFrame()

        # ===== PDF / Impressão =====
        has_df = isinstance(df_view, pd.DataFrame) and (not df_view.empty)
        if has_df:
            try:
                pdf_bytes = gerar_pdf(
                    df_view, stats_cp_idade, fig1, fig2, fig3, fig4,
                    str(df_view["Obra"].mode().iat[0]) if "Obra" in df_view.columns and not df_view["Obra"].dropna().empty else "—",
                    str(df_view["Data Certificado"].mode().iat[0]) if "Data Certificado" in df_view.columns and not df_view["Data Certificado"].dropna().empty else "—",
                    _format_float_label(fck_active),
                    verif_fck_df, cond_df, pareamento_df, pv  # <<< inclui verificação detalhada no PDF
                )
                _nome_pdf = "Relatorio_Graficos.pdf"
                st.download_button("📄 Baixar Relatório (PDF)", data=pdf_bytes, file_name=_nome_pdf, mime="application/pdf")
            except Exception as e:
                st.error(f"Falha ao gerar PDF: {e}")

            # ===== Exportação: Excel (XLSX) e CSV (ZIP) =====
            try:
                stats_all_full = (
                    df_view.groupby("Idade (dias)")["Resistência (MPa)"]
                          .agg(mean="mean", std="std", count="count").reset_index()
                )
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
                    df_view.to_excel(writer, sheet_name="Individuais", index=False)
                    stats_cp_idade.to_excel(writer, sheet_name="Médias_DP", index=False)
                    comp_df = stats_all_full.rename(columns={"mean":"Média Real","std":"DP Real","count":"n"})
                    if isinstance(est_df, pd.DataFrame) and (not est_df.empty):
                        comp_df = comp_df.merge(est_df.rename(columns={"Resistência (MPa)":"Estimado"}), on="Idade (dias)", how="outer").sort_values("Idade (dias)")
                        comp_df.to_excel(writer, sheet_name="Comparação", index=False)
                    if not pv.empty:
                        pv.to_excel(writer, sheet_name="Detalhado_CP", index=False)
                    # imagens
                    try:
                        ws_md = writer.sheets.get("Médias_DP")
                        if ws_md is not None and fig1 is not None:
                            img1 = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                            fig1.savefig(img1.name, dpi=150, bbox_inches="tight")
                            ws_md.insert_image("H2", img1.name, {"x_scale": 0.72, "y_scale": 0.72})
                    except Exception:
                        pass
                    try:
                        ws_comp = writer.sheets.get("Comparação")
                        if ws_comp is not None and fig2 is not None:
                            img2 = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                            fig2.savefig(img2.name, dpi=150, bbox_inches="tight")
                            ws_comp.insert_image("H20", img2.name, {"x_scale": 0.72, "y_scale": 0.72})
                        if ws_comp is not None and fig3 is not None:
                            img3 = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                            fig3.savefig(img3.name, dpi=150, bbox_inches="tight")
                            ws_comp.insert_image("H38", img3.name, {"x_scale": 0.72, "y_scale": 0.72})
                    except Exception:
                        pass
                st.download_button("📊 Baixar Excel (XLSX)", data=excel_buffer.getvalue(), file_name="Relatorio_Graficos.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

                zip_buf = io.BytesIO()
                with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("Individuais.csv", df_view.to_csv(index=False, sep=";"))
                    z.writestr("Medias_DP.csv", stats_cp_idade.to_csv(index=False, sep=";"))
                    if isinstance(est_df, pd.DataFrame) and (not est_df.empty):
                        z.writestr("Estimativas.csv", est_df.to_csv(index=False, sep=";"))
                    if "comp_df" in locals():
                        z.writestr("Comparacao.csv", comp_df.to_csv(index=False, sep=";"))
                    if not pv.empty:
                        z.writestr("Detalhado_CP.csv", pv.to_csv(index=False, sep=";"))
                st.download_button("🗃️ Baixar CSVs (ZIP)", data=zip_buf.getvalue(), file_name="Relatorio_Graficos_CSVs.zip",
                                   mime="application/zip", use_container_width=True)
            except Exception:
                pass
else:
    st.info("Envie um PDF para visualizar os gráficos, relatório e exportações.")

# 5) Ler Novo(s) Certificado(s)
if st.button("📂 Ler Novo(s) Certificado(s)", use_container_width=True, key="btn_novo"):
    s["uploader_key"] += 1; st.rerun()

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
      Sistema desenvolvido pela Habisolute Engenharia
    </div>
    """,
    unsafe_allow_html=True
)

    

