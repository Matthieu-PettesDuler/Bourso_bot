#!/usr/bin/env python3
"""
Agent Trading Matthieu v9
Nouveautés vs v8 :
- MACD (12/26/9) — détecte les croisements de tendance
- Volume relatif — confirme la force des signaux
- Bandes de Bollinger — mesure la volatilité
- Score de confiance global (0-100) combinant tous les indicateurs
- Signal combiné : RSI + MACD + Volume + Bollinger → moins de faux signaux
- Portefeuille mis à jour (Thales 5 actions @ 250.08EUR)
- Heure Paris UTC+2 (07:00 et 15:30 UTC)
"""

import os, yfinance as yf, requests, anthropic, schedule, time, feedparser, json
from datetime import datetime
from pathlib import Path
import pytz

# ============================================================
# CONFIGURATION
# ============================================================
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MEMOIRE_FILE      = "/tmp/memoire_matthieu.json"
PARIS_TZ          = pytz.timezone("Europe/Paris")
SEUIL_ALERTE      = 3.0

# ============================================================
# PORTEFEUILLE RÉEL — MIS À JOUR AVRIL 2026
# ============================================================
SEUILS = {
    # CTO — Positions réelles
    "ORA.PA":  {"nom": "Orange",            "achat": 15.50, "vente": 20.00, "type": "CTO",     "secteur": "Telecom",      "quantite": 83,  "px_revient": 10.70},
    "CAP.PA":  {"nom": "Capgemini",         "achat": 85.00, "vente": 130.00,"type": "CTO",     "secteur": "IA/Tech",      "quantite": 4,   "px_revient": 131.07},
    "TTE.PA":  {"nom": "TotalEnergies",     "achat": 68.00, "vente": 95.00, "type": "CTO",     "secteur": "Energie",      "quantite": 12,  "px_revient": 78.84},
    "BNP.PA":  {"nom": "BNP Paribas",       "achat": 72.00, "vente": 100.00,"type": "CTO",     "secteur": "Banque",       "quantite": 3,   "px_revient": 85.51},
    "AIR.PA":  {"nom": "Airbus",            "achat": 145.00,"vente": 195.00,"type": "CTO",     "secteur": "Aerospatiale", "quantite": 3,   "px_revient": 166.78},
    "SAF.PA":  {"nom": "Safran",            "achat": 250.00,"vente": 340.00,"type": "CTO",     "secteur": "Defense",      "quantite": 2,   "px_revient": 289.87},
    "HO.PA":   {"nom": "Thales",            "achat": 200.00,"vente": 310.00,"type": "CTO",     "secteur": "Defense/IA",   "quantite": 5,   "px_revient": 250.08},
    "AM.PA":   {"nom": "Dassault Aviation", "achat": 280.00,"vente": 380.00,"type": "CTO",     "secteur": "Defense",      "quantite": 2,   "px_revient": 328.05},
    "SU.PA":   {"nom": "Schneider Electric","achat": 200.00,"vente": 310.00,"type": "CTO",     "secteur": "Energie/IA",   "quantite": 3,   "px_revient": 270.33},
    "MSFT":    {"nom": "Microsoft",         "achat": 300.00,"vente": 480.00,"type": "CTO-US",  "secteur": "IA/Cloud",     "quantite": 2,   "px_revient": 325.84},
    # Surveillance
    "DSY.PA":  {"nom": "Dassault Systemes", "achat": 15.00, "vente": 38.00, "type": "WATCH",   "secteur": "Tech/IA"},
    "EN.PA":   {"nom": "Edenred",           "achat": 40.00, "vente": 60.00, "type": "WATCH",   "secteur": "Fintech"},
    "NVDA":    {"nom": "Nvidia",            "achat": 100.00,"vente": 220.00,"type": "WATCH-US","secteur": "IA/Puces"},
    "GE":      {"nom": "GE Aerospace",      "achat": 240.00,"vente": 370.00,"type": "WATCH-US","secteur": "Defense"},
    # PEA
    "CW8.PA":  {"nom": "Bourso Monde",      "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF World"},
    "ERO.PA":  {"nom": "Bourso Europe",     "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF Europe"},
    # Baromètres
    "^FCHI":   {"nom": "CAC 40",            "achat": None,  "vente": None,  "type": "INDEX",   "secteur": "Indice"},
    "GC=F":    {"nom": "Or",                "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Refuge"},
    "CL=F":    {"nom": "Petrole WTI",       "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Energie"},
}

CORRELATIONS = {
    "TTE.PA": "TotalEnergies suit le WTI à ~85% de corrélation",
    "BNP.PA": "BNP monte quand BCE remonte les taux",
    "AIR.PA": "Airbus chute lors des guerres commerciales US/EU",
    "SAF.PA": "Safran monte avec les budgets défense européens",
    "HO.PA":  "Thales bénéficie du réarmement européen",
    "AM.PA":  "Dassault Aviation liée au Rafale et budget défense",
    "SU.PA":  "Schneider profite de l'électrification et des data centers IA",
    "ORA.PA": "Orange résiste en crise, dividende stable depuis 22 ans",
    "CAP.PA": "Capgemini suit la demande IA/IT des entreprises",
    "MSFT":   "Microsoft bénéficie de l'IA via Azure et OpenAI",
}

RSS_FEEDS = [
    {"url": "https://feeds.reuters.com/reuters/businessNews", "label": "Reuters"},
    {"url": "https://feeds.reuters.com/Reuters/worldNews",    "label": "Reuters Monde"},
    {"url": "https://www.boursorama.com/rss/actu-societes",   "label": "Boursorama"},
]

KEYWORDS_PORTEFEUILLE = ["orange", "bnp", "total", "capgemini", "airbus", "safran",
                          "thales", "dassault", "schneider", "microsoft", "nvidia"]
KEYWORDS_MACRO = ["trump", "taxe", "guerre", "iran", "ukraine", "russie", "chine",
                   "fed", "bce", "taux", "recession", "petrole", "inflation",
                   "intelligence artificielle", "rearmement", "ormuz", "cessez-le-feu"]

# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(message):
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/sendMessage"
    chunks = []
    while len(message) > 4000:
        cut = message[:4000].rfind("\n")
        if cut < 0: cut = 4000
        chunks.append(message[:cut])
        message = message[cut:]
    chunks.append(message)
    for chunk in chunks:
        try:
            r = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML"
            }, timeout=10)
            r.raise_for_status()
            print("[" + datetime.now(PARIS_TZ).strftime("%H:%M") + "] Telegram OK")
            time.sleep(0.5)
        except Exception as e:
            print("[ERREUR Telegram] " + str(e))

# ============================================================
# ÉCOUTE MESSAGES TELEGRAM
# ============================================================
last_update_id = None

def check_messages_telegram():
    global last_update_id
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/getUpdates"
    params = {"timeout": 1}
    if last_update_id:
        params["offset"] = last_update_id
    try:
        r = requests.get(url, params=params, timeout=5)
        updates = r.json()
    except:
        return
    for update in updates.get("result", []):
        last_update_id = update["update_id"] + 1
        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if not text or chat_id != str(TELEGRAM_CHAT_ID):
            continue
        print("[MSG] " + text)
        if "backtest" in text.lower():
            resultats = backtest_decisions()
            if not resultats:
                send_telegram("Pas encore assez de décisions mémorisées.")
                return
            lignes = ["📊 <b>Backtest de tes décisions :</b>"]
            for r in resultats:
                lignes.append("{} {} | {} | {:+.1f}%".format(
                    r["verdict"], r["valeur"], r["date"], r["perf"]))
            send_telegram("\n".join(lignes))
            return
        donnees = [calcul_indicateurs(t) for t in SEUILS.keys()]
        donnees_ok = [d for d in donnees if d]
        news_p, news_m = get_news()
        sentiment = get_sentiment(donnees_ok)
        reponse = analyse_claude(donnees_ok, "temps réel", news_p, news_m, sentiment, question_user=text)
        send_telegram("🤖 <b>Agent :</b>\n" + reponse)

# ============================================================
# INDICATEURS TECHNIQUES v9
# Nouveautés : MACD, Bollinger, Volume, Score de confiance
# ============================================================
def ema(closes, periode):
    """Moyenne mobile exponentielle — Python pur"""
    if len(closes) < periode:
        return None
    k = 2 / (periode + 1)
    ema_val = sum(closes[:periode]) / periode
    for c in closes[periode:]:
        ema_val = c * k + ema_val * (1 - k)
    return round(ema_val, 4)

def calcul_indicateurs(ticker):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="6mo", interval="1d")
        if len(hist) < 26:
            return None

        closes  = hist["Close"].values.tolist()
        volumes = hist["Volume"].values.tolist()
        c = round(float(closes[-1]), 2)
        h = round(float(closes[-2]), 2) if len(closes) > 1 else c
        variation = round((c - h) / h * 100, 2)

        # ── RSI 14 ──────────────────────────────────────────
        deltas    = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
        gains     = [d if d > 0 else 0 for d in deltas]
        pertes    = [-d if d < 0 else 0 for d in deltas]
        avg_gain  = sum(gains[-14:])  / 14 if len(gains)  >= 14 else sum(gains)  / max(len(gains), 1)
        avg_perte = sum(pertes[-14:]) / 14 if len(pertes) >= 14 else sum(pertes) / max(len(pertes), 1)
        rsi = round(100 - (100 / (1 + avg_gain / avg_perte)) if avg_perte > 0 else 100, 1)

        # ── Moyennes mobiles ────────────────────────────────
        mm20  = round(sum(closes[-20:])  / 20,  2) if len(closes) >= 20  else None
        mm50  = round(sum(closes[-50:])  / 50,  2) if len(closes) >= 50  else None
        mm200 = round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else None

        # ── MACD (12/26/9) ──────────────────────────────────
        ema12 = ema(closes, 12)
        ema26 = ema(closes, 26)
        macd_line = round(ema12 - ema26, 4) if ema12 and ema26 else None

        # Signal MACD : EMA 9 de la ligne MACD
        # On calcule la ligne MACD sur tout l'historique
        macd_signal = None
        macd_hist   = None
        macd_croise = "NEUTRE"
        if len(closes) >= 35:
            macd_series = []
            for i in range(26, len(closes)+1):
                e12 = ema(closes[:i], 12)
                e26 = ema(closes[:i], 26)
                if e12 and e26:
                    macd_series.append(e12 - e26)
            if len(macd_series) >= 9:
                macd_signal = round(ema(macd_series, 9), 4)
                macd_hist   = round(macd_series[-1] - macd_signal, 4)
                # Croisement haussier : MACD passe au-dessus du signal
                if len(macd_series) >= 2:
                    prev_diff = macd_series[-2] - (ema(macd_series[:-1], 9) or macd_signal)
                    curr_diff = macd_hist
                    if prev_diff < 0 and curr_diff > 0:
                        macd_croise = "HAUSSIER"  # Signal d'achat
                    elif prev_diff > 0 and curr_diff < 0:
                        macd_croise = "BAISSIER"  # Signal de vente

        # ── Bandes de Bollinger (20j, 2 écarts-types) ───────
        bb_haut = bb_bas = bb_signal = None
        if len(closes) >= 20:
            mm20_val = sum(closes[-20:]) / 20
            variance = sum((c - mm20_val)**2 for c in closes[-20:]) / 20
            ecart    = variance ** 0.5
            bb_haut  = round(mm20_val + 2 * ecart, 2)
            bb_bas   = round(mm20_val - 2 * ecart, 2)
            if c <= bb_bas:
                bb_signal = "SURVENDU"    # Cours sous la bande basse → achat potentiel
            elif c >= bb_haut:
                bb_signal = "SURACHETÉ"   # Cours au-dessus bande haute → prudence
            else:
                pct = round((c - bb_bas) / (bb_haut - bb_bas) * 100, 0) if bb_haut != bb_bas else 50
                bb_signal = "{}% bande".format(int(pct))

        # ── Volume relatif (ratio 5j / moyenne 20j) ─────────
        vol_moy20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
        vol_rec5  = sum(volumes[-5:])  / 5  if len(volumes) >= 5  else None
        vol_ratio = round(vol_rec5 / vol_moy20, 2) if vol_moy20 and vol_rec5 and vol_moy20 > 0 else 1.0
        vol_signal = "FORT" if vol_ratio > 1.5 else "FAIBLE" if vol_ratio < 0.7 else "NORMAL"

        # ── Tendance 1 mois ──────────────────────────────────
        t1m = round((closes[-1] - closes[-22]) / closes[-22] * 100, 1) if len(closes) >= 22 else None

        # ── Score de confiance combiné (0-100) ───────────────
        # Combine RSI + MACD + Volume + Bollinger pour réduire les faux signaux
        score_achat  = 0
        score_vente  = 0
        signaux_achat = []
        signaux_vente = []

        if rsi < 30:
            score_achat += 35
            signaux_achat.append("RSI survendu ({})".format(rsi))
        elif rsi > 70:
            score_vente += 35
            signaux_vente.append("RSI suracheté ({})".format(rsi))

        if macd_croise == "HAUSSIER":
            score_achat += 30
            signaux_achat.append("MACD croisement haussier")
        elif macd_croise == "BAISSIER":
            score_vente += 30
            signaux_vente.append("MACD croisement baissier")

        if bb_signal == "SURVENDU":
            score_achat += 20
            signaux_achat.append("Bollinger bande basse")
        elif bb_signal == "SURACHETÉ":
            score_vente += 20
            signaux_vente.append("Bollinger bande haute")

        if vol_ratio > 1.5:
            if variation > 0:
                score_achat += 15
                signaux_achat.append("Volume fort haussier x{:.1f}".format(vol_ratio))
            else:
                score_vente += 15
                signaux_vente.append("Volume fort baissier x{:.1f}".format(vol_ratio))

        if mm50 and c > mm50:
            score_achat += 10
            signaux_achat.append("Prix > MM50")
        elif mm50 and c < mm50:
            score_vente += 5

        score_achat = min(100, score_achat)
        score_vente = min(100, score_vente)

        # Signal final
        signal = "NEUTRE"
        if score_achat >= 50:
            signal = "ACHAT FORT"
        elif score_achat >= 35:
            signal = "ACHAT"
        elif score_vente >= 50:
            signal = "VENTE FORT"
        elif score_vente >= 35:
            signal = "VENTE"
        elif rsi < 30:
            signal = "SURVENDU"
        elif rsi > 70:
            signal = "SURACHETÉ"
        elif mm50 and c > mm50:
            signal = "HAUSSIER"
        elif mm50 and c < mm50:
            signal = "BAISSIER"

        try:
            info     = t.fast_info
            high_52w = round(float(info.year_high), 2) if hasattr(info, "year_high") else None
            low_52w  = round(float(info.year_low),  2) if hasattr(info, "year_low")  else None
        except:
            high_52w, low_52w = None, None

        return {
            "ticker": ticker, "cours": c, "hier": h, "variation": variation,
            "rsi": rsi, "mm20": mm20, "mm50": mm50, "mm200": mm200,
            "macd_line": macd_line, "macd_signal": macd_signal,
            "macd_hist": macd_hist, "macd_croise": macd_croise,
            "bb_haut": bb_haut, "bb_bas": bb_bas, "bb_signal": bb_signal,
            "vol_ratio": vol_ratio, "vol_signal": vol_signal,
            "tendance_1m": t1m, "signal_tech": signal,
            "score_achat": score_achat, "score_vente": score_vente,
            "signaux_achat": signaux_achat, "signaux_vente": signaux_vente,
            "high_52w": high_52w, "low_52w": low_52w
        }
    except Exception as e:
        print("[ERREUR " + ticker + "] " + str(e))
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d", interval="1d")
            if hist.empty: return None
            c = round(float(hist["Close"].iloc[-1]), 2)
            h = round(float(hist["Close"].iloc[-2]), 2) if len(hist) > 1 else c
            return {"ticker": ticker, "cours": c, "hier": h,
                    "variation": round((c-h)/h*100, 2),
                    "rsi": None, "mm50": None, "mm200": None,
                    "macd_croise": "INCONNU", "bb_signal": None,
                    "vol_ratio": 1.0, "vol_signal": "NORMAL",
                    "tendance_1m": None, "signal_tech": "INCONNU",
                    "score_achat": 0, "score_vente": 0,
                    "signaux_achat": [], "signaux_vente": [],
                    "high_52w": None, "low_52w": None}
        except:
            return None

def rsi_emoji(rsi):
    if rsi is None: return ""
    if rsi < 30:   return " 🟢RSI{:.0f}".format(rsi)
    if rsi > 70:   return " 🔴RSI{:.0f}".format(rsi)
    return " RSI{:.0f}".format(rsi)

def score_emoji(score_achat, score_vente):
    if score_achat >= 50:   return " 🎯{}%".format(score_achat)
    if score_vente >= 50:   return " ⚠️{}%".format(score_vente)
    if score_achat >= 35:   return " 👀{}%".format(score_achat)
    return ""

# ============================================================
# MÉMOIRE & BACKTESTING
# ============================================================
def load_memoire():
    try:
        if Path(MEMOIRE_FILE).exists():
            with open(MEMOIRE_FILE) as f:
                return json.load(f)
    except: pass
    return {"decisions": [], "stats": {"bonnes": 0, "mauvaises": 0}}

def save_memoire(m):
    try:
        with open(MEMOIRE_FILE, "w") as f:
            json.dump(m, f, ensure_ascii=False)
    except: pass

def backtest_decisions():
    m = load_memoire()
    resultats = []
    for d in m.get("decisions", []):
        ticker = None
        for k, v in SEUILS.items():
            if v["nom"].lower() in d.get("valeur", "").lower():
                ticker = k
                break
        if not ticker: continue
        data = calcul_indicateurs(ticker)
        if not data: continue
        px = d.get("prix", 0)
        if px and data["cours"]:
            perf = round((data["cours"] - px) / px * 100, 1)
            resultats.append({
                "valeur": d["valeur"], "date": d["date"],
                "perf": perf, "verdict": "✅" if perf > 0 else "❌"
            })
    return resultats

# ============================================================
# NEWS
# ============================================================
def get_news():
    news_p, news_m = [], []
    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:30]:
                title = entry.get("title", "")
                tl    = title.lower()
                if any(kw in tl for kw in KEYWORDS_PORTEFEUILLE) and title not in news_p:
                    news_p.append(title)
                elif any(kw in tl for kw in KEYWORDS_MACRO) and title not in news_m:
                    news_m.append(title)
        except: pass
    return news_p[:3], news_m[:3]

def get_sentiment(donnees):
    types = ["CTO", "CTO-US", "WATCH", "WATCH-US"]
    h = sum(1 for d in donnees if d and d["variation"] > 0 and SEUILS.get(d["ticker"],{}).get("type") in types)
    b = sum(1 for d in donnees if d and d["variation"] < 0 and SEUILS.get(d["ticker"],{}).get("type") in types)
    total = h + b
    if total == 0: return "NEUTRE"
    if h/total >= 0.65: return "HAUSSIER"
    if h/total <= 0.35: return "BAISSIER"
    return "NEUTRE"

def calcul_pv(ticker, cours):
    s = SEUILS.get(ticker, {})
    if s.get("px_revient") and s.get("quantite"):
        return round((cours - s["px_revient"]) * s["quantite"], 2)
    return None

def pv_totale(donnees):
    total = 0
    for d in donnees:
        if not d: continue
        pv = calcul_pv(d["ticker"], d["cours"])
        if pv: total += pv
    return round(total, 2)

# ============================================================
# ANALYSE CLAUDE v9 — enrichie avec MACD + Bollinger + Volume
# ============================================================
def analyse_claude(donnees, moment, news_p, news_m, sentiment, question_user=None):
    if not ANTHROPIC_API_KEY:
        return "Clé Claude manquante."
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    m = load_memoire()
    decisions_str = "\n".join([
        "- {}: {} {} a {}EUR".format(d["date"], d["action"], d["valeur"], d["prix"])
        for d in m.get("decisions", [])[-5:]
    ]) or "Aucune"

    lignes = []
    for d in donnees:
        if not d: continue
        s = SEUILS.get(d["ticker"], {})
        if s["type"] not in ["CTO", "CTO-US", "WATCH", "WATCH-US"]: continue
        pv   = calcul_pv(d["ticker"], d["cours"])
        corr = CORRELATIONS.get(d["ticker"], "")

        # Score combiné
        score_str = ""
        if d.get("score_achat", 0) >= 35:
            score_str = " 🎯SCORE_ACHAT:{}".format(d["score_achat"])
            if d.get("signaux_achat"):
                score_str += "({})".format("+".join(d["signaux_achat"]))
        elif d.get("score_vente", 0) >= 35:
            score_str = " ⚠️SCORE_VENTE:{}".format(d["score_vente"])

        lignes.append("- {} {}EUR ({}{}%) RSI:{} MACD:{} BB:{} Vol:{} T1M:{}% [{}]{}{} {}".format(
            s.get("nom",""), d["cours"],
            "+" if d["variation"]>=0 else "", d["variation"],
            d.get("rsi","?"),
            d.get("macd_croise","?"),
            d.get("bb_signal","?"),
            d.get("vol_signal","?"),
            d.get("tendance_1m","?"),
            d.get("signal_tech",""),
            " PV:{:+.0f}EUR".format(pv) if pv is not None else "",
            score_str,
            "| "+corr if corr else ""
        ))

    macro = []
    for d in donnees:
        if not d: continue
        s = SEUILS.get(d["ticker"], {})
        if s["type"] in ["INDEX", "MATIERES"]:
            macro.append("{}: {} ({}{}%)".format(
                s["nom"], d["cours"],
                "+" if d["variation"]>=0 else "", d["variation"]))

    question_str = "\nQUESTION : " + question_user if question_user else ""

    prompt = """Tu es l'agent financier de Matthieu, investisseur français débutant.

PORTEFEUILLE (CTO Boursobank, flat tax 30%, horizon 1 an) :
- Orange : 83 @ 10.70EUR → dividende juin ~100EUR nets
- Capgemini : 4 @ 131.07EUR
- TotalEnergies : 12 @ 78.84EUR (corrélation pétrole 85%)
- BNP Paribas : 3 @ 85.51EUR
- Airbus : 3 @ 166.78EUR
- Safran : 2 @ 289.87EUR
- Thales : 5 @ 250.08EUR (RSI survendu depuis 1 semaine)
- Dassault Aviation : 2 @ 328.05EUR
- Schneider Electric : 3 @ 270.33EUR
- Microsoft : 2 @ 325.84EUR
Cash disponible : ~0EUR (quasi vide)

INDICATEURS DISPONIBLES (v9) :
- RSI 14j : survendu <30, suracheté >70
- MACD 12/26/9 : croisement haussier = signal achat, baissier = signal vente
- Bandes de Bollinger : bande basse = survendu, bande haute = suracheté
- Volume relatif : x>1.5 = fort = confirme le signal
- Score de confiance 0-100 : combine tous les indicateurs

RÈGLE FONDAMENTALE :
Un bon signal = RSI survendu + MACD haussier + Volume fort = Score > 50
Un signal seul (RSI uniquement) = moins fiable = Score 35-50 = surveiller

MARCHÉS {} — {} :
Macro : {}
{}

NEWS : {} | {}
SENTIMENT : {}
{}

ANALYSE (250 mots max) :
1. Résumé macro béton (1 phrase)
2. Top 3 signaux les plus forts (score > 35) avec justification multi-indicateurs
3. PROPOSITION D'ORDRE si score > 50 (sinon "Surveiller X quand score dépasse 50") :
   FORMAT : ACTION | VALEUR | QUANTITÉ | PRIX | TYPE ORDRE | SCORE | RAISON
4. Risque : FAIBLE/MODÉRÉ/ÉLEVÉ""".format(
        moment.upper(),
        datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
        " | ".join(macro),
        "\n".join(lignes),
        " | ".join(news_p) if news_p else "RAS",
        " | ".join(news_m) if news_m else "RAS",
        sentiment,
        question_str)

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}])
        return msg.content[0].text
    except Exception as e:
        return "[Erreur Claude : " + str(e) + "]"

# ============================================================
# ANALYSE COMPLÈTE
# ============================================================
def analyse_complete(moment):
    now = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
    print("\n[" + now + "] Analyse " + moment + "...")

    donnees    = [calcul_indicateurs(t) for t in SEUILS.keys()]
    donnees_ok = [d for d in donnees if d]
    if not donnees_ok:
        send_telegram("Marchés fermés ou erreur réseau.")
        return

    news_p, news_m = get_news()
    sentiment   = get_sentiment(donnees_ok)
    sent_emoji  = "🟢" if sentiment == "HAUSSIER" else "🔴" if sentiment == "BAISSIER" else "🟡"
    pv          = pv_totale(donnees_ok)

    sections = [
        ("📊 Marchés",      ["INDEX", "MATIERES"]),
        ("💼 Portefeuille", ["CTO", "CTO-US"]),
        ("👁 Surveillance", ["WATCH", "WATCH-US"]),
        ("📈 PEA",          ["PEA"]),
    ]

    lignes_msg    = []
    alertes_seuil = []

    for titre, types in sections:
        bloc = []
        for d in donnees_ok:
            s = SEUILS[d["ticker"]]
            if s["type"] not in types: continue
            f = "🟢" if d["variation"] >= 0 else "🔴"

            if s["type"] in ["INDEX", "MATIERES"]:
                bloc.append("{} <b>{}</b>  {}  {}{}%".format(
                    f, s["nom"], d["cours"],
                    "+" if d["variation"]>=0 else "", d["variation"]))
            else:
                pv_ligne  = calcul_pv(d["ticker"], d["cours"])
                pv_str    = " <i>{:+.0f}€</i>".format(pv_ligne) if pv_ligne is not None else ""
                rsi_str   = rsi_emoji(d.get("rsi"))
                score_str = score_emoji(d.get("score_achat",0), d.get("score_vente",0))
                t1m_str   = " T1M:{:+.1f}%".format(d["tendance_1m"]) if d.get("tendance_1m") is not None else ""
                macd_str  = " MACD:{}".format(d.get("macd_croise","")) if d.get("macd_croise") not in ["NEUTRE","INCONNU",None] else ""

                l = "{} <b>{}</b>  {}EUR  {}{}%{}{}{}{}{}".format(
                    f, s["nom"], d["cours"],
                    "+" if d["variation"]>=0 else "", d["variation"],
                    pv_str, rsi_str, score_str, t1m_str, macd_str)

                if s.get("achat") and d["cours"] <= s["achat"]:
                    l += "\n   🎯 Zone achat !"
                    alertes_seuil.append("🎯 {} zone achat".format(s["nom"]))
                if s.get("vente") and d["cours"] >= s["vente"]:
                    l += "\n   💰 Zone vente !"
                    alertes_seuil.append("💰 {} zone vente".format(s["nom"]))
                bloc.append(l)
        if bloc:
            lignes_msg.append("\n<b>{}</b>\n".format(titre) + "\n".join(bloc))

    emoji   = "🌅" if moment == "matin" else "🌆"
    analyse = analyse_claude(donnees_ok, moment, news_p, news_m, sentiment)

    news_bloc = ""
    if news_p or news_m:
        news_bloc = "\n📰 <b>News :</b>\n" + "\n".join(
            ["• " + n[:80] for n in (news_p + news_m)[:3]]) + "\n"

    alertes_bloc = "\n🚨 " + " | ".join(alertes_seuil) + "\n" if alertes_seuil else ""

    msg = ("{} <b>Analyse {} — {}</b>\n"
           "{} Sentiment : <b>{}</b>  💰 PV : <b>{:+.0f}€</b>\n"
           "――――――――――――――――――――――\n"
           "{}\n"
           "――――――――――――――――――――――"
           "{}{}\n"
           "――――――――――――――――――――――\n"
           "🤖 <b>Signal agent v9 :</b>\n{}\n"
           "――――――――――――――――――――――\n"
           "<i>Réponds ici | 'backtest' pour tes perf</i>").format(
        emoji, moment.upper(), now,
        sent_emoji, sentiment, pv,
        "\n".join(lignes_msg),
        news_bloc, alertes_bloc, analyse)

    send_telegram(msg)
    m = load_memoire()
    m["derniere_analyse"] = now
    save_memoire(m)
    print("[" + now + "] OK")

# ============================================================
# ALERTES INTRADAY — 30min, variation > 3%
# ============================================================
def check_alertes_intraday():
    now = datetime.now(PARIS_TZ)
    if now.weekday() >= 5: return
    if now.hour < 9 or (now.hour == 17 and now.minute >= 30) or now.hour > 17: return

    tickers = ["ORA.PA","CAP.PA","TTE.PA","BNP.PA","AIR.PA",
               "SAF.PA","HO.PA","AM.PA","SU.PA","MSFT","^FCHI"]
    alertes = []
    action  = "\n⚡ <b>Action :</b> Réponds ici pour analyse immédiate."

    for ticker in tickers:
        d = calcul_indicateurs(ticker)
        if not d or abs(d["variation"]) < SEUIL_ALERTE: continue
        s  = SEUILS.get(ticker, {})
        f  = "📈" if d["variation"] > 0 else "📉"
        pv = calcul_pv(ticker, d["cours"])

        # Score de confiance dans l'alerte
        score = d.get("score_achat", 0) if d["variation"] < 0 else d.get("score_vente", 0)
        score_str = " 🎯Score:{}".format(score) if score >= 35 else ""

        alertes.append("{} <b>{}</b> {}EUR {}{}% RSI:{}{} Vol:{}".format(
            f, s.get("nom", ticker), d["cours"],
            "+" if d["variation"]>=0 else "", d["variation"],
            d.get("rsi","?"), score_str,
            d.get("vol_signal","?")))

        if d["variation"] <= -5.0:
            action = "\n⚡ <b>Action :</b> Baisse forte. Ne vends pas. Score achat:{} — Réponds ici.".format(d.get("score_achat",0))
        elif d.get("score_achat",0) >= 50:
            action = "\n⚡ <b>Action :</b> 🎯 SIGNAL FORT achat (score {}). Réponds ici pour valider.".format(d["score_achat"])
        elif d.get("score_vente",0) >= 50:
            action = "\n⚡ <b>Action :</b> ⚠️ SIGNAL FORT vente (score {}). Réponds ici pour décider.".format(d["score_vente"])
        elif d.get("rsi") and d["rsi"] < 30:
            action = "\n⚡ <b>Action :</b> RSI survendu ({}) — surveille le MACD pour confirmer.".format(d["rsi"])
        elif d.get("rsi") and d["rsi"] > 70:
            action = "\n⚡ <b>Action :</b> RSI suracheté ({}) — attention correction.".format(d["rsi"])

    if alertes:
        _, news_m = get_news()
        ctx = "\n📰 " + news_m[0][:90] if news_m else ""
        msg = ("🚨 <b>ALERTE — " + now.strftime("%H:%M") + "</b>\n"
               "――――――――――――――――――――――\n" +
               "\n".join(alertes) + ctx + action +
               "\n――――――――――――――――――――――\n"
               "<i>Réponds directement ici !</i>")
        send_telegram(msg)

def analyse_matin(): analyse_complete("matin")
def analyse_soir():  analyse_complete("soir")

# ============================================================
# BOUCLE PRINCIPALE
# ============================================================
if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not ANTHROPIC_API_KEY:
        print("[ERREUR] Variables Railway manquantes")
        exit(1)

    print("=" * 55)
    print("  Agent Trading Matthieu v9")
    print("  MACD + Bollinger + Volume + Score confiance")
    print("  Heure Paris : 09:00 et 17:30 (07:00/15:30 UTC)")
    print("  Alertes 30min si variation > 3%")
    print("=" * 55)

    send_telegram(
        "🚀 <b>Agent Trading v9 — Signaux renforcés !</b>\n\n"
        "✅ MACD 12/26/9 — croisements de tendance\n"
        "✅ Bandes de Bollinger — zones de survente/surachat\n"
        "✅ Volume relatif — confirme la force des signaux\n"
        "✅ Score de confiance 0-100 — combine tous les indicateurs\n"
        "✅ Moins de faux signaux — signal fort = score > 50\n"
        "✅ RSI + MACD + Bollinger + Volume = fiabilité maximale\n\n"
        "Analyses : 9h00 et 17h30 heure Paris 🎯")

    # UTC : 07:00 = 09:00 Paris / 15:30 = 17:30 Paris (UTC+2 été)
    schedule.every().day.at("07:00").do(analyse_matin)
    schedule.every().day.at("15:30").do(analyse_soir)
    schedule.every(30).minutes.do(check_alertes_intraday)

    while True:
        schedule.run_pending()
        check_messages_telegram()
        time.sleep(10)
