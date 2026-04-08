#!/usr/bin/env python3
import os, yfinance as yf, requests, anthropic, schedule, time, feedparser, json
from datetime import datetime
from pathlib import Path
import pytz


TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MEMOIRE_FILE      = "/tmp/memoire_matthieu.json"

# ============================================================
# PORTEFEUILLE RÉEL
# ============================================================
SEUILS = {
    "ORA.PA":  {"nom": "Orange",       "achat": 15.50, "vente": 20.00, "type": "CTO",     "secteur": "Telecom",      "quantite": 133, "px_revient": 10.70},
    "CAP.PA":  {"nom": "Capgemini",    "achat": 85.00, "vente": 130.00,"type": "CTO",     "secteur": "IA/Tech",      "quantite": 2,   "px_revient": 161.03},
    "TTE.PA":  {"nom": "TotalEnergies","achat": 68.00, "vente": 95.00, "type": "CTO",     "secteur": "Energie",      "quantite": 7,   "px_revient": 80.15},
    "BNP.PA":  {"nom": "BNP Paribas",  "achat": 72.00, "vente": 100.00,"type": "CTO",     "secteur": "Banque",       "quantite": 5,   "px_revient": 85.51},
    "AIR.PA":  {"nom": "Airbus",       "achat": 145.00,"vente": 195.00,"type": "CTO",     "secteur": "Aerospatiale", "quantite": 3,   "px_revient": 166.78},
    "SAF.PA":  {"nom": "Safran",       "achat": 250.00,"vente": 340.00,"type": "CTO",     "secteur": "Defense",      "quantite": 2,   "px_revient": 289.87},
    "AM.PA":   {"nom": "Dassault",     "achat": 290.00,"vente": 380.00,"type": "WATCH",   "secteur": "Defense"},
    "HO.PA":   {"nom": "Thales",       "achat": 220.00,"vente": 310.00,"type": "WATCH",   "secteur": "Defense/IA"},
    "SU.PA":   {"nom": "Schneider",    "achat": 200.00,"vente": 290.00,"type": "WATCH",   "secteur": "Energie/IA"},
    "MSFT":    {"nom": "Microsoft",    "achat": 340.00,"vente": 480.00,"type": "WATCH-US","secteur": "IA/Cloud"},
    "NVDA":    {"nom": "Nvidia",       "achat": 100.00,"vente": 200.00,"type": "WATCH-US","secteur": "IA/Puces"},
    "GE":      {"nom": "GE Aerospace", "achat": 240.00,"vente": 370.00,"type": "WATCH-US","secteur": "Defense"},
    "CW8.PA":  {"nom": "Bourso Monde", "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF World"},
    "ERO.PA":  {"nom": "Bourso Europe","achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF Europe"},
    "^FCHI":   {"nom": "CAC 40",       "achat": None,  "vente": None,  "type": "INDEX",   "secteur": "Indice"},
    "GC=F":    {"nom": "Or",           "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Refuge"},
    "CL=F":    {"nom": "Petrole WTI",  "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Energie"},
}

# Corrélations historiques connues (robustification du modèle)
CORRELATIONS = {
    "TTE.PA":  {"petrole": "+fort",   "note": "TotalEnergies suit le WTI à ~85% de corrélation"},
    "BNP.PA":  {"taux_bce": "+fort",  "note": "BNP monte quand BCE remonte les taux"},
    "AIR.PA":  {"trump": "-fort",     "note": "Airbus chute lors des guerres commerciales US/EU"},
    "SAF.PA":  {"defense": "+fort",   "note": "Safran monte avec les budgets défense européens"},
    "ORA.PA":  {"defensif": "oui",    "note": "Orange résiste en crise, dividende stable depuis 22 ans"},
    "CAP.PA":  {"ia": "+moyen",       "note": "Capgemini suit la demande IA/IT des entreprises"},
    "GC=F":    {"crise": "+fort",     "note": "Or monte en période d'incertitude géopolitique"},
    "CL=F":    {"iran": "+fort",      "note": "Pétrole monte si Détroit d'Ormuz menacé"},
}

SEUIL_ALERTE_VARIATION = 3.0
PARIS_TZ = pytz.timezone("Europe/Paris")

RSS_FEEDS = [
    {"url": "https://feeds.reuters.com/reuters/businessNews", "label": "Reuters"},
    {"url": "https://feeds.reuters.com/Reuters/worldNews",    "label": "Reuters Monde"},
    {"url": "https://www.boursorama.com/rss/actu-societes",   "label": "Boursorama"},
]

KEYWORDS_PORTEFEUILLE = ["orange", "bnp", "total", "capgemini", "airbus", "safran", "dassault", "thales", "schneider", "microsoft", "nvidia"]
KEYWORDS_MACRO = ["trump", "taxe", "guerre", "iran", "ukraine", "russie", "chine", "fed", "bce", "taux", "recession", "petrole", "inflation", "intelligence artificielle", "rearmement", "ormuz"]

# ============================================================
# INDICATEURS TECHNIQUES
# ============================================================
def calcul_indicateurs(ticker):
    """RSI, MM50, MM200, tendance sur 6 mois d'historique"""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="6mo", interval="1d")
        if len(hist) < 20:
            return None

        closes = hist["Close"].values.astype(float)
        c = round(float(closes[-1]), 2)
        h = round(float(closes[-2]), 2) if len(closes) > 1 else c
        variation = round((c - h) / h * 100, 2)

        # RSI 14
        deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
        gains = [d if d > 0 else 0 for d in deltas]
        pertes = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains[-14:])/len(gains[-14:]) if len(gains) >= 14 else sum(gains)/max(len(gains),1)
        avg_perte = sum(pertes[-14:])/len(pertes[-14:]) if len(pertes) >= 14 else sum(pertes)/max(len(pertes),1)
        rsi = round(100 - (100 / (1 + avg_gain / avg_perte)) if avg_perte > 0 else 100, 1)

        # Moyennes mobiles
        mm20  = round(sum(closes[-20:])/20, 2) if len(closes) >= 20 else None
        mm50  = round(sum(closes[-50:])/50, 2) if len(closes) >= 50 else None
        mm200 = round(sum(closes[-200:])/200, 2) if len(closes) >= 200 else None

        # Tendance 1 mois
        tendance_1m = round((closes[-1] - closes[-22]) / closes[-22] * 100, 1) if len(closes) >= 22 else None

        # Signal technique
        signal_tech = "NEUTRE"
        if rsi < 30:
            signal_tech = "SURVENDU"   # opportunité achat
        elif rsi > 70:
            signal_tech = "SURACHETÉ"  # attention vente
        elif mm50 and c > mm50 and (mm200 is None or c > mm200):
            signal_tech = "HAUSSIER"
        elif mm50 and c < mm50:
            signal_tech = "BAISSIER"

        # Volume relatif
        try:
            info = t.fast_info
            high_52w = round(float(info.year_high), 2) if hasattr(info, "year_high") else None
            low_52w  = round(float(info.year_low), 2)  if hasattr(info, "year_low")  else None
        except:
            high_52w, low_52w = None, None

        return {
            "ticker": ticker, "cours": c, "hier": h, "variation": variation,
            "rsi": rsi, "mm20": mm20, "mm50": mm50, "mm200": mm200,
            "tendance_1m": tendance_1m, "signal_tech": signal_tech,
            "high_52w": high_52w, "low_52w": low_52w
        }
    except Exception as e:
        print("[ERREUR indicateurs " + ticker + "] " + str(e))
        # Fallback cours simple
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d", interval="1d")
            if hist.empty: return None
            c = round(float(hist["Close"].iloc[-1]), 2)
            h = round(float(hist["Close"].iloc[-2]), 2) if len(hist) > 1 else c
            v = round((c - h) / h * 100, 2)
            return {"ticker": ticker, "cours": c, "hier": h, "variation": v,
                    "rsi": None, "mm50": None, "mm200": None,
                    "tendance_1m": None, "signal_tech": "INCONNU",
                    "high_52w": None, "low_52w": None}
        except:
            return None

def signal_rsi_emoji(rsi):
    if rsi is None: return ""
    if rsi < 30: return "🟢RSI{:.0f}(SURVENDU)".format(rsi)
    if rsi > 70: return "🔴RSI{:.0f}(SURACHETÉ)".format(rsi)
    return "RSI{:.0f}".format(rsi)

# ============================================================
# MÉMOIRE + BACKTESTING
# ============================================================
def load_memoire():
    try:
        if Path(MEMOIRE_FILE).exists():
            with open(MEMOIRE_FILE) as f:
                return json.load(f)
    except: pass
    return {"decisions": [], "backtest": [], "stats": {"bonnes": 0, "mauvaises": 0}}

def save_memoire(m):
    try:
        with open(MEMOIRE_FILE, "w") as f:
            json.dump(m, f, ensure_ascii=False)
    except: pass

def backtest_decisions():
    """Vérifie si les recommandations passées étaient bonnes"""
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
        prix_decision = d.get("prix", 0)
        if prix_decision and data["cours"]:
            perf = round((data["cours"] - prix_decision) / prix_decision * 100, 1)
            resultats.append({
                "valeur": d["valeur"],
                "date": d["date"],
                "signal": d.get("signal", "?"),
                "prix_decision": prix_decision,
                "cours_actuel": data["cours"],
                "perf": perf,
                "verdict": "✅" if perf > 0 else "❌"
            })
    return resultats

# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(message):
    """Envoie un message Telegram, découpe si > 3800 chars avec fallback sans HTML"""
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/sendMessage"
    chunks = []
    while len(message) > 3800:
        cut = message.rfind("\n", 0, 3800)
        if cut == -1:
            cut = 3800
        chunks.append(message[:cut])
        message = message[cut:].lstrip("\n")
    chunks.append(message)

    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        try:
            r = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML"
            }, timeout=10)
            r.raise_for_status()
            if i < len(chunks) - 1:
                time.sleep(1)
            print("[" + datetime.now().strftime("%H:%M") + "] OK")
        except Exception as e:
            # Retry sans HTML si erreur de parsing HTML
            try:
                clean = chunk.replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>","")
                r = requests.post(url, json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": clean
                }, timeout=10)
                r.raise_for_status()
                print("[" + datetime.now().strftime("%H:%M") + "] OK (plain)")
            except Exception as e2:
                print("[ERREUR Telegram] " + str(e2))
            # Retry sans HTML si erreur de parsing
            try:
                clean = chunk.replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>","")
                requests.post(url, json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": clean,
                    "parse_mode": None
                }, timeout=10)
            except:
                pass

last_update_id = None
def check_messages_telegram():
    global last_update_id
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/getUpdates"
    params = {"timeout": 1}
    if last_update_id: params["offset"] = last_update_id
    try:
        r = requests.get(url, params=params, timeout=5)
        updates = r.json()
    except: return
    for update in updates.get("result", []):
        last_update_id = update["update_id"] + 1
        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if not text or chat_id != str(TELEGRAM_CHAT_ID): continue
        print("[MSG] " + text)
        # Commande spéciale backtesting
        if "backtest" in text.lower():
            resultats = backtest_decisions()
            if not resultats:
                send_telegram("Pas encore assez de décisions mémorisées pour un backtest.")
                return
            lignes = ["📊 <b>Backtest de tes décisions :</b>"]
            for r in resultats:
                lignes.append("{} {} | {} | {:+.1f}%".format(
                    r["verdict"], r["valeur"], r["date"], r["perf"]))
            send_telegram("\n".join(lignes))
            return
        # Réponse agent normale
        donnees = [calcul_indicateurs(t) for t in SEUILS.keys()]
        donnees_ok = [d for d in donnees if d]
        news_p, news_m = get_news()
        sentiment = get_sentiment(donnees_ok)
        reponse = analyse_claude(donnees_ok, "temps réel", news_p, news_m, sentiment, question_user=text)
        send_telegram("🤖 <b>Agent :</b>\n" + reponse)

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
                tl = title.lower()
                if any(kw in tl for kw in KEYWORDS_PORTEFEUILLE) and title not in news_p:
                    news_p.append(title)
                elif any(kw in tl for kw in KEYWORDS_MACRO) and title not in news_m:
                    news_m.append(title)
        except: pass
    return news_p[:3], news_m[:3]

def get_sentiment(donnees):
    types = ["CTO", "WATCH", "WATCH-US"]
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

def valeur_totale_portefeuille(donnees):
    total = 0
    for d in donnees:
        if not d: continue
        pv = calcul_pv(d["ticker"], d["cours"])
        if pv: total += pv
    return round(total, 2)

# ============================================================
# ANALYSE CLAUDE — enrichie avec indicateurs techniques + corrélations
# ============================================================
def analyse_claude(donnees, moment, news_p, news_m, sentiment, question_user=None):
    if not ANTHROPIC_API_KEY:
        return "Cle Claude manquante."
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    m = load_memoire()
    decisions_str = "\n".join([
        "- {}: {} {} a {}EUR".format(d["date"], d["action"], d["valeur"], d["prix"])
        for d in m.get("decisions", [])[-5:]
    ]) or "Aucune"

    # Données enrichies avec indicateurs techniques
    lignes = []
    for d in donnees:
        if not d: continue
        s = SEUILS.get(d["ticker"], {})
        if s["type"] not in ["CTO", "WATCH", "WATCH-US"]: continue
        pv = calcul_pv(d["ticker"], d["cours"])
        pv_str = " PV:{:+.0f}EUR".format(pv) if pv is not None else ""
        rsi_str = " RSI:{:.0f}".format(d["rsi"]) if d.get("rsi") else ""
        mm50_str = " MM50:{}".format(d["mm50"]) if d.get("mm50") else ""
        tendance_str = " T1M:{:+.1f}%".format(d["tendance_1m"]) if d.get("tendance_1m") is not None else ""
        signal_str = " [{}]".format(d.get("signal_tech","")) if d.get("signal_tech") else ""
        corr = CORRELATIONS.get(d["ticker"], {})
        corr_str = " | " + corr.get("note","") if corr.get("note") else ""
        lignes.append("- {} {}EUR ({}{}%){}{}{}{}{}{}" .format(
            s.get("nom",""), d["cours"],
            "+" if d["variation"]>=0 else "", d["variation"],
            pv_str, rsi_str, mm50_str, tendance_str, signal_str, corr_str))

    # Contexte macro
    macro_data = []
    for d in donnees:
        if not d: continue
        s = SEUILS.get(d["ticker"], {})
        if s["type"] in ["INDEX", "MATIERES"]:
            macro_data.append("{}: {}  {}{}%".format(
                s["nom"], d["cours"],
                "+" if d["variation"]>=0 else "", d["variation"]))

    question_str = "\nQUESTION : " + question_user if question_user else ""

    prompt = """Tu es l'agent financier de Matthieu. Tu disposes des indicateurs techniques et des corrélations historiques.

PORTEFEUILLE :
Orange(133@10.70) | Capgemini(2@161) | TotalEnergies(7@80.15) | BNP(5@85.51) | Airbus(3@166.78) | Safran(2@289.87)
Cash disponible : ~896EUR | CTO flat tax 30% | Horizon 1 an

CORRÉLATIONS HISTORIQUES CLÉS :
- Pétrole monte → TotalEnergies monte (corr. 85%)
- BCE baisse taux → BNP monte
- Guerre commerciale Trump → Airbus chute
- Conflit Moyen-Orient → Or monte + Pétrole monte
- Réarmement Europe → Safran/Dassault/Thales montent
- Crise générale → Orange résiste (défensif)

DÉCISIONS MÉMORISÉES :
{}

MARCHÉS {} — {} :
Macro: {}
{}

NEWS : {} | {}
SENTIMENT : {}
{}

ANALYSE ROBUSTE (indicateurs techniques + corrélations) :
1. Résumé béton avec contexte macro (1 phrase)
2. Signal technique pour chaque position CTO :
   - RSI < 30 = survendu = opportunité achat
   - RSI > 70 = suracheté = attention
   - Prix > MM50 = tendance haussière
   - Tendance 1 mois
3. PROPOSITION D'ORDRE si signal fort :
   FORMAT : ACTION | VALEUR | QUANTITÉ | PRIX LIMITE | ORDRE | RAISON TECHNIQUE
4. Risque : FAIBLE/MODÉRÉ/ÉLEVÉ avec justification

Max 250 mots. Factuel et actionnable.""".format(
        decisions_str, moment.upper(),
        datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
        " | ".join(macro_data),
        "\n".join(lignes),
        " | ".join(news_p) if news_p else "RAS",
        " | ".join(news_m) if news_m else "RAS",
        sentiment, question_str)

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
    donnees = [calcul_indicateurs(t) for t in SEUILS.keys()]
    donnees_ok = [d for d in donnees if d]
    if not donnees_ok:
        send_telegram("Marches fermes.")
        return

    news_p, news_m = get_news()
    sentiment = get_sentiment(donnees_ok)
    sent_emoji = "🟢" if sentiment == "HAUSSIER" else "🔴" if sentiment == "BAISSIER" else "🟡"
    pv_total = valeur_totale_portefeuille(donnees_ok)

    sections = [
        ("📊 Marchés", ["INDEX", "MATIERES"]),
        ("💼 Portefeuille", ["CTO"]),
        ("👁 Surveillance", ["WATCH", "WATCH-US"]),
        ("📈 PEA", ["PEA"]),
    ]

    lignes_msg = []
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
                pv = calcul_pv(d["ticker"], d["cours"])
                pv_str = " <i>{:+.0f}€</i>".format(pv) if pv is not None else ""
                rsi_str = " <i>{}</i>".format(signal_rsi_emoji(d.get("rsi"))) if d.get("rsi") else ""
                t1m_str = " <i>T1M:{:+.1f}%</i>".format(d["tendance_1m"]) if d.get("tendance_1m") is not None else ""
                l = "{} <b>{}</b>  {}EUR  {}{}%{}{}{}".format(
                    f, s["nom"], d["cours"],
                    "+" if d["variation"]>=0 else "", d["variation"],
                    pv_str, rsi_str, t1m_str)
                if s["achat"] and d["cours"] <= s["achat"]:
                    l += "\n   🎯 Zone achat !"
                    alertes_seuil.append("🎯 {} zone achat".format(s["nom"]))
                if s["vente"] and d["cours"] >= s["vente"]:
                    l += "\n   💰 Zone vente !"
                    alertes_seuil.append("💰 {} zone vente".format(s["nom"]))
                bloc.append(l)
        if bloc:
            lignes_msg.append("\n<b>{}</b>\n".format(titre) + "\n".join(bloc))

    emoji = "🌅" if moment == "matin" else "🌆"
    analyse = analyse_claude(donnees_ok, moment, news_p, news_m, sentiment)

    news_bloc = ""
    if news_p or news_m:
        news_bloc = "\n📰 <b>News :</b>\n" + "\n".join(["• " + n[:80] for n in (news_p + news_m)[:3]]) + "\n"

    alertes_bloc = "\n🚨 " + " | ".join(alertes_seuil) + "\n" if alertes_seuil else ""

    msg = ("{} <b>Analyse {} — {}</b>\n"
           "{} Sentiment : <b>{}</b>  💰 PV: <b>{:+.0f}€</b>\n"
           "――――――――――――――――――――――\n"
           "{}\n"
           "――――――――――――――――――――――"
           "{}{}\n"
           "――――――――――――――――――――――\n"
           "🤖 <b>Signal agent :</b>\n{}\n"
           "――――――――――――――――――――――\n"
           "<i>Réponds ici | 'backtest' pour vérifier tes décisions</i>").format(
        emoji, moment.upper(), now,
        sent_emoji, sentiment, pv_total,
        "\n".join(lignes_msg),
        news_bloc, alertes_bloc, analyse)

    send_telegram(msg)
    print("[" + now + "] OK")

# ============================================================
# ALERTES INTRADAY
# ============================================================
def check_alertes_intraday():
    now = datetime.now(PARIS_TZ)
    if now.weekday() >= 5: return
    if now.hour < 9 or (now.hour >= 17 and now.minute >= 30): return

    tickers_cto = ["ORA.PA", "CAP.PA", "TTE.PA", "BNP.PA", "AIR.PA", "SAF.PA", "^FCHI"]
    alertes = []
    action = ""

    for ticker in tickers_cto:
        d = calcul_indicateurs(ticker)
        if not d or abs(d["variation"]) < SEUIL_ALERTE_VARIATION: continue
        s = SEUILS.get(ticker, {})
        f = "📈" if d["variation"] > 0 else "📉"
        pv = calcul_pv(ticker, d["cours"])
        pv_str = " ({:+.0f}€)".format(pv) if pv else ""
        rsi_str = " RSI:{:.0f}".format(d["rsi"]) if d.get("rsi") else ""
        alertes.append("{} <b>{}</b> {}EUR {}{}%{}{}".format(
            f, s.get("nom", ticker), d["cours"],
            "+" if d["variation"]>=0 else "", d["variation"],
            pv_str, rsi_str))
        # Détermine l'action
        corr = CORRELATIONS.get(ticker, {})
        if d["variation"] <= -5.0:
            action = "\n⚡ <b>Action :</b> Baisse forte. " + corr.get("note","") + " Ne vends pas. Réponds ici."
        elif d["variation"] >= 5.0 and s.get("vente") and d["cours"] >= s["vente"]:
            action = "\n⚡ <b>Action :</b> Zone de vente atteinte. Réponds ici pour décider."
        elif d.get("rsi") and d["rsi"] < 30:
            action = "\n⚡ <b>Action :</b> RSI survendu — opportunité d'achat potentielle. Réponds ici."
        elif d.get("rsi") and d["rsi"] > 70:
            action = "\n⚡ <b>Action :</b> RSI suracheté — attention à une correction. Surveille."
        else:
            action = "\n⚡ <b>Action :</b> Variation notable. Réponds ici pour analyse immédiate."

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

    print("=" * 50)
    print("  Agent Trading Matthieu v7")
    print("  RSI + MM50 + MM200 + correlations historiques")
    print("  Backtesting des decisions")
    print("  Reponse Telegram directe")
    print("=" * 50)

    send_telegram(
        "🚀 <b>Agent v7 — Robustifié !</b>\n\n"
        "✅ RSI 14 jours sur toutes les valeurs\n"
        "✅ Moyennes mobiles MM20/MM50/MM200\n"
        "✅ Tendance 1 mois historique\n"
        "✅ Corrélations : pétrole↔Total, BCE↔BNP, Trump↔Airbus...\n"
        "✅ Backtesting : réponds 'backtest' pour voir tes perf\n"
        "✅ Alertes RSI survendu/suracheté\n"
        "✅ Mémoire + propositions d'ordres précises\n\n"
        "Réponds 'backtest' pour tester l'historique 👇")

    schedule.every().day.at("07:00").do(analyse_matin)
    schedule.every().day.at("15:30").do(analyse_soir)
    schedule.every(30).minutes.do(check_alertes_intraday)

    while True:
        schedule.run_pending()
        check_messages_telegram()
        time.sleep(10)
