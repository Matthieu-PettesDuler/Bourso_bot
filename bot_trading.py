#!/usr/bin/env python3
import os, yfinance as yf, requests, anthropic, schedule, time, feedparser, json
from datetime import datetime
from pathlib import Path
import pytz

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

MEMOIRE_FILE = "/tmp/memoire_matthieu.json"

SEUILS = {
    "ORA.PA":  {"nom": "Orange",          "achat": 15.50, "vente": 20.00, "type": "CTO",     "secteur": "Telecom",         "quantite": 133, "px_revient": 10.70},
    "CAP.PA":  {"nom": "Capgemini",       "achat": 85.00, "vente": 130.00,"type": "CTO",     "secteur": "IA/Tech",         "quantite": 2,   "px_revient": 161.03},
    "TTE.PA":  {"nom": "TotalEnergies",   "achat": 68.00, "vente": 95.00, "type": "CTO",     "secteur": "Energie",         "quantite": 7,   "px_revient": 80.15},
    "BNP.PA":  {"nom": "BNP Paribas",     "achat": 72.00, "vente": 100.00,"type": "CTO",     "secteur": "Banque",          "quantite": 5,   "px_revient": 85.51},
    "AIR.PA":  {"nom": "Airbus",          "achat": 145.00,"vente": 195.00,"type": "CTO",     "secteur": "Aerospatiale",    "quantite": 3,   "px_revient": 166.78},
    "SAF.PA":  {"nom": "Safran",          "achat": 250.00,"vente": 340.00,"type": "CTO",     "secteur": "Defense/Moteurs", "quantite": 2,   "px_revient": 289.87},
    "AM.PA":   {"nom": "Dassault",        "achat": 290.00,"vente": 380.00,"type": "WATCH",   "secteur": "Defense"},
    "HO.PA":   {"nom": "Thales",          "achat": 220.00,"vente": 310.00,"type": "WATCH",   "secteur": "Defense/IA"},
    "SU.PA":   {"nom": "Schneider",       "achat": 200.00,"vente": 290.00,"type": "WATCH",   "secteur": "Energie/IA"},
    "MSFT":    {"nom": "Microsoft",       "achat": 340.00,"vente": 480.00,"type": "WATCH-US","secteur": "IA/Cloud"},
    "NVDA":    {"nom": "Nvidia",          "achat": 100.00,"vente": 200.00,"type": "WATCH-US","secteur": "IA/Puces"},
    "GE":      {"nom": "GE Aerospace",    "achat": 240.00,"vente": 370.00,"type": "WATCH-US","secteur": "Defense/Moteurs"},
    "CW8.PA":  {"nom": "Bourso Monde",    "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF World"},
    "ERO.PA":  {"nom": "Bourso Europe",   "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF Europe"},
    "^FCHI":   {"nom": "CAC 40",          "achat": None,  "vente": None,  "type": "INDEX",   "secteur": "Indice"},
    "GC=F":    {"nom": "Or",              "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Refuge"},
    "CL=F":    {"nom": "Petrole WTI",     "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Energie"},
}

SEUIL_ALERTE_VARIATION = 3.0
PARIS_TZ = pytz.timezone("Europe/Paris")

RSS_FEEDS = [
    {"url": "https://feeds.reuters.com/reuters/businessNews", "label": "Reuters Business"},
    {"url": "https://feeds.reuters.com/Reuters/worldNews",    "label": "Reuters Monde"},
    {"url": "https://www.boursorama.com/rss/actu-societes",   "label": "Boursorama"},
]

KEYWORDS_PORTEFEUILLE = ["orange", "bnp", "total", "capgemini", "airbus", "safran", "dassault", "thales", "schneider", "microsoft", "nvidia"]
KEYWORDS_MACRO = ["trump", "taxe douaniere", "droits de douane", "guerre", "iran", "ukraine", "russie", "chine", "fed", "bce", "taux", "recession", "petrole", "inflation", "intelligence artificielle", "rearmement"]

# ============================================================
# MÉMOIRE PERSISTANTE
# ============================================================
def load_memoire():
    try:
        if Path(MEMOIRE_FILE).exists():
            with open(MEMOIRE_FILE) as f:
                return json.load(f)
    except:
        pass
    return {"decisions": [], "stats": {"bonnes": 0, "mauvaises": 0}, "derniere_analyse": ""}

def save_memoire(m):
    try:
        with open(MEMOIRE_FILE, "w") as f:
            json.dump(m, f, ensure_ascii=False)
    except:
        pass

def ajouter_decision(action, valeur, prix, raison, signal):
    m = load_memoire()
    m["decisions"].append({
        "date": datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
        "action": action, "valeur": valeur,
        "prix": prix, "raison": raison, "signal": signal
    })
    m["decisions"] = m["decisions"][-20:]  # garde les 20 dernières
    save_memoire(m)

# ============================================================
# FONCTIONS UTILITAIRES
# ============================================================
def send_telegram(message):
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
        r.raise_for_status()
        print("[" + datetime.now().strftime("%H:%M") + "] Telegram OK")
    except Exception as e:
        print("[ERREUR Telegram] " + str(e))

def get_updates(offset=None):
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/getUpdates"
    params = {"timeout": 1}
    if offset:
        params["offset"] = offset
    try:
        r = requests.get(url, params=params, timeout=5)
        return r.json()
    except:
        return {"ok": False, "result": []}

def get_cours(ticker):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", interval="1d")
        if hist.empty:
            return None
        info = t.fast_info
        c = round(float(hist["Close"].iloc[-1]), 2)
        h = round(float(hist["Close"].iloc[-2]), 2) if len(hist) > 1 else c
        v = round((c - h) / h * 100, 2)
        return {
            "ticker": ticker, "cours": c, "hier": h, "variation": v,
            "high_52w": round(float(info.year_high), 2) if hasattr(info, "year_high") else None,
            "low_52w":  round(float(info.year_low), 2)  if hasattr(info, "year_low")  else None,
        }
    except Exception as e:
        print("[ERREUR " + ticker + "] " + str(e))
        return None

def get_news():
    news_portfolio, news_macro = [], []
    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:30]:
                title = entry.get("title", "")
                tl = title.lower()
                if any(kw in tl for kw in KEYWORDS_PORTEFEUILLE) and title not in news_portfolio:
                    news_portfolio.append(title)
                elif any(kw in tl for kw in KEYWORDS_MACRO) and title not in news_macro:
                    news_macro.append(title)
        except:
            pass
    return news_portfolio[:3], news_macro[:3]

def get_sentiment(donnees):
    types = ["CTO", "WATCH", "WATCH-US"]
    h = sum(1 for d in donnees if d and d["variation"] > 0 and SEUILS.get(d["ticker"],{}).get("type") in types)
    b = sum(1 for d in donnees if d and d["variation"] < 0 and SEUILS.get(d["ticker"],{}).get("type") in types)
    total = h + b
    if total == 0: return "NEUTRE"
    ratio = h / total
    if ratio >= 0.65: return "HAUSSIER"
    elif ratio <= 0.35: return "BAISSIER"
    return "NEUTRE"

def calcul_pv(ticker, cours):
    s = SEUILS.get(ticker, {})
    if s.get("px_revient") and s.get("quantite"):
        return round((cours - s["px_revient"]) * s["quantite"], 2)
    return None

def valeur_totale_portefeuille():
    total_pv = 0
    for ticker, s in SEUILS.items():
        if s["type"] == "CTO" and s.get("quantite") and s.get("px_revient"):
            d = get_cours(ticker)
            if d:
                pv = calcul_pv(ticker, d["cours"])
                if pv:
                    total_pv += pv
    return round(total_pv, 2)

# ============================================================
# ANALYSE CLAUDE — avec mémoire et propositions d'ordres
# ============================================================
def analyse_claude(donnees, moment, news_portfolio, news_macro, sentiment, question_user=None):
    if not ANTHROPIC_API_KEY:
        return "Cle Claude manquante."
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    m = load_memoire()
    decisions_recentes = m["decisions"][-5:] if m["decisions"] else []
    decisions_str = "\n".join(["- {}: {} {} a {}EUR ({})".format(
        d["date"], d["action"], d["valeur"], d["prix"], d["raison"]
    ) for d in decisions_recentes]) if decisions_recentes else "Aucune"

    lignes = []
    for d in donnees:
        if not d: continue
        s = SEUILS.get(d["ticker"], {})
        pv = calcul_pv(d["ticker"], d["cours"])
        pv_str = " PV:{:+.0f}EUR".format(pv) if pv is not None else ""
        lignes.append("- {} [{}] {}EUR ({}{}%){}".format(
            s.get("nom", d["ticker"]), s.get("type",""),
            d["cours"], "+" if d["variation"]>=0 else "", d["variation"], pv_str))

    question_str = "\nQUESTION DE MATTHIEU : " + question_user if question_user else ""

    prompt = """Tu es l'agent financier personnel de Matthieu. Tu as accès à sa mémoire de décisions.

PORTEFEUILLE :
- Orange : 133 actions, px 10.70EUR, div. juin ~160EUR nets
- Capgemini : 2 actions, px 161EUR
- TotalEnergies : 7 actions, px 80.15EUR
- BNP Paribas : 5 actions, px 85.51EUR
- Airbus : 3 actions, px 166.78EUR
- Safran : 2 actions, px 289.87EUR
- PEA : Bourso Monde 200EUR + Europe 100EUR/mois

RÈGLES :
- CTO : flat tax 30% sur plus-values
- Horizon 1 an
- Risque modéré
- Pas d'achat impulsif sans analyse

DÉCISIONS RÉCENTES :
{}

MARCHÉS {} — {} :
{}

NEWS : {} | {}
SENTIMENT : {}
{}

RÉPONDS AVEC :
1. Résumé (1 phrase béton, basée sur les faits)
2. Impact sur chaque position réelle
3. PROPOSITION D'ORDRE CONCRÈTE si opportunité (sinon "Rien à faire") :
   Format : ACTION | VALEUR | QUANTITÉ | PRIX LIMITE | TYPE ORDRE | RAISON
4. Risque : FAIBLE/MODÉRÉ/ÉLEVÉ
5. Mémorise si une décision est prise

Max 220 mots. Sois direct et factuel.""".format(
        decisions_str,
        moment.upper(),
        datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
        "\n".join(lignes),
        " | ".join(news_portfolio) if news_portfolio else "RAS",
        " | ".join(news_macro) if news_macro else "RAS",
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
# RÉPONSE AUX MESSAGES TELEGRAM (mode agent)
# ============================================================
last_update_id = None

def check_messages_telegram():
    global last_update_id
    updates = get_updates(offset=last_update_id)
    if not updates.get("ok"):
        return
    for update in updates.get("result", []):
        last_update_id = update["update_id"] + 1
        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = msg.get("chat", {}).get("id")
        if not text or str(chat_id) != str(TELEGRAM_CHAT_ID):
            continue
        print("[MSG TELEGRAM] " + text)
        # Répond à toute question directement
        donnees = [get_cours(t) for t in SEUILS.keys()]
        donnees_ok = [d for d in donnees if d]
        news_p, news_m = get_news()
        sentiment = get_sentiment(donnees_ok)
        reponse = analyse_claude(donnees_ok, "temps réel", news_p, news_m, sentiment, question_user=text)
        send_telegram("🤖 <b>Réponse agent :</b>\n" + reponse)

# ============================================================
# ANALYSE COMPLÈTE
# ============================================================
def analyse_complete(moment):
    now = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
    print("\n[" + now + "] Analyse " + moment + "...")
    donnees = [get_cours(t) for t in SEUILS.keys()]
    donnees_ok = [d for d in donnees if d]
    if not donnees_ok:
        send_telegram("Marches fermes ou erreur reseau.")
        return

    news_p, news_m = get_news()
    sentiment = get_sentiment(donnees_ok)
    sent_emoji = "🟢" if sentiment == "HAUSSIER" else "🔴" if sentiment == "BAISSIER" else "🟡"

    sections = [
        ("📊 Marchés", ["INDEX", "MATIERES"]),
        ("💼 Ton portefeuille", ["CTO"]),
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
                    f, s["nom"], d["cours"], "+" if d["variation"]>=0 else "", d["variation"]))
            else:
                pv = calcul_pv(d["ticker"], d["cours"])
                pv_str = "  <i>{:+.0f}€</i>".format(pv) if pv is not None else ""
                l = "{} <b>{}</b>  {}EUR  {}{}%{}".format(
                    f, s["nom"], d["cours"], "+" if d["variation"]>=0 else "", d["variation"], pv_str)
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

    pv_total = valeur_totale_portefeuille()
    pv_ligne = "\n💰 <b>PV totale portefeuille : {:+.0f}€</b>".format(pv_total)

    msg = ("{} <b>Analyse {} — {}</b>\n"
           "{} Sentiment : <b>{}</b>{}\n"
           "――――――――――――――――――――――\n"
           "{}\n"
           "――――――――――――――――――――――"
           "{}{}\n"
           "――――――――――――――――――――――\n"
           "🤖 <b>Signal agent :</b>\n{}\n"
           "――――――――――――――――――――――\n"
           "<i>Réponds directement ici pour interagir</i>").format(
        emoji, moment.upper(), now,
        sent_emoji, sentiment, pv_ligne,
        "\n".join(lignes_msg),
        news_bloc, alertes_bloc, analyse)

    send_telegram(msg)
    m = load_memoire()
    m["derniere_analyse"] = now
    save_memoire(m)
    print("[" + now + "] OK")

def check_alertes_intraday():
    now = datetime.now(PARIS_TZ)
    if now.weekday() >= 5: return
    if now.hour < 9 or (now.hour >= 17 and now.minute >= 30): return

    alertes = []
    tickers_cto = ["ORA.PA", "CAP.PA", "TTE.PA", "BNP.PA", "AIR.PA", "SAF.PA", "^FCHI"]
    for ticker in tickers_cto:
        d = get_cours(ticker)
        if not d or abs(d["variation"]) < SEUIL_ALERTE_VARIATION: continue
        s = SEUILS.get(ticker, {})
        f = "📈" if d["variation"] > 0 else "📉"
        pv = calcul_pv(ticker, d["cours"])
        pv_str = " ({:+.0f}€)".format(pv) if pv else ""
        alertes.append("{} <b>{}</b> {}EUR {}{}%{}".format(
            f, s.get("nom", ticker), d["cours"],
            "+" if d["variation"]>=0 else "", d["variation"], pv_str))

    if alertes:
        _, news_m = get_news()
        ctx = "\n📰 " + news_m[0][:90] if news_m else ""

        # Action recommandée automatique
        action = "\n⚡ <b>Action :</b> Surveille. Réponds à ce message pour une analyse immédiate."
        for ticker in tickers_cto:
            d = get_cours(ticker)
            if not d or abs(d["variation"]) < SEUIL_ALERTE_VARIATION: continue
            s = SEUILS.get(ticker, {})
            if d["variation"] <= -5.0:
                action = "\n⚡ <b>Action :</b> Baisse forte. Ne vends pas. Réponds ici pour analyse."
            elif d["variation"] >= 5.0 and s.get("vente") and d["cours"] >= s["vente"]:
                action = "\n⚡ <b>Action :</b> Zone de vente atteinte. Réponds ici pour décider."
            elif d["variation"] >= 5.0 and s.get("achat") and d["cours"] <= s["achat"]:
                action = "\n⚡ <b>Action :</b> Zone d'achat. Réponds ici pour valider l'entrée."
            break

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
    print("  Bot Agent Matthieu v6")
    print("  Mode agent : repond aux messages Telegram")
    print("  Analyses : 09:00 et 17:30 Paris")
    print("  Alertes : toutes les 30min si >3%")
    print("=" * 50)

    send_telegram(
        "🚀 <b>Agent Trading v6 !</b>\n\n"
        "✅ Tu peux me répondre directement sur Telegram\n"
        "✅ Je mémorise toutes tes décisions\n"
        "✅ Je propose des ordres précis (valeur, prix, quantité)\n"
        "✅ Alertes 30min avec action recommandée\n"
        "✅ PV totale portefeuille en temps réel\n\n"
        "Essaie de me répondre 'Analyse Airbus' 👇")

    schedule.every().day.at("07:00").do(analyse_matin)
    schedule.every().day.at("15:30").do(analyse_soir)
    schedule.every(30).minutes.do(check_alertes_intraday)

    # Boucle avec écoute des messages Telegram
    while True:
        schedule.run_pending()
        check_messages_telegram()
        time.sleep(10)
