#!/usr/bin/env python3
import os, yfinance as yf, requests, anthropic, schedule, time, feedparser
from datetime import datetime
import pytz

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ============================================================
# PORTEFEUILLE RÉEL DE MATTHIEU
# Seuils recalibrés : achat = -10% du cours actuel, vente = objectif analystes
# ============================================================
SEUILS = {
    # CTO France — positions réelles
    "ORA.PA":  {"nom": "Orange",          "achat": 15.50, "vente": 20.00, "type": "CTO",    "secteur": "Telecom",         "quantite": 133},
    "CAP.PA":  {"nom": "Capgemini",       "achat": 85.00, "vente": 130.00,"type": "CTO",    "secteur": "IA/Tech",         "quantite": 2},
    "TTE.PA":  {"nom": "TotalEnergies",   "achat": 68.00, "vente": 95.00, "type": "CTO",    "secteur": "Energie",         "quantite": 7},
    "BNP.PA":  {"nom": "BNP Paribas",     "achat": 72.00, "vente": 100.00,"type": "CTO",    "secteur": "Banque",          "quantite": 5},
    "AIR.PA":  {"nom": "Airbus",          "achat": 145.00,"vente": 195.00,"type": "CTO",    "secteur": "Aerospatiale",    "quantite": 3},
    "SAF.PA":  {"nom": "Safran",          "achat": 250.00,"vente": 340.00,"type": "CTO",    "secteur": "Defense/Moteurs", "quantite": 2},
    # CTO France — surveillance (pas encore achetées)
    "AM.PA":   {"nom": "Dassault Aviation","achat": 290.00,"vente": 380.00,"type": "WATCH",  "secteur": "Defense"},
    "HO.PA":   {"nom": "Thales",          "achat": 220.00,"vente": 310.00,"type": "WATCH",  "secteur": "Defense/IA"},
    "SU.PA":   {"nom": "Schneider Elec.", "achat": 200.00,"vente": 290.00,"type": "WATCH",  "secteur": "Energie/IA"},
    # CTO USA — surveillance
    "MSFT":    {"nom": "Microsoft",       "achat": 340.00,"vente": 480.00,"type": "WATCH-US","secteur": "IA/Cloud"},
    "NVDA":    {"nom": "Nvidia",          "achat": 100.00,"vente": 200.00,"type": "WATCH-US","secteur": "IA/Puces"},
    "GE":      {"nom": "GE Aerospace",    "achat": 240.00,"vente": 370.00,"type": "WATCH-US","secteur": "Defense/Moteurs"},
    # PEA
    "CW8.PA":  {"nom": "Bourso Monde",    "achat": None,  "vente": None,  "type": "PEA",    "secteur": "ETF World"},
    "ERO.PA":  {"nom": "Bourso Europe",   "achat": None,  "vente": None,  "type": "PEA",    "secteur": "ETF Europe"},
    # Indices & Matières — baromètres du marché
    "^FCHI":   {"nom": "CAC 40",          "achat": None,  "vente": None,  "type": "INDEX",  "secteur": "Indice"},
    "GC=F":    {"nom": "Or",              "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Refuge"},
    "CL=F":    {"nom": "Petrole WTI",     "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Energie"},
}

# Seuil d'alerte intraday — uniquement si variation > 3% (plus de bruit)
SEUIL_ALERTE_VARIATION = 3.0

PARIS_TZ = pytz.timezone("Europe/Paris")

RSS_FEEDS = [
    {"url": "https://feeds.reuters.com/reuters/businessNews", "label": "Reuters Business"},
    {"url": "https://feeds.reuters.com/Reuters/worldNews",    "label": "Reuters Monde"},
    {"url": "https://www.boursorama.com/rss/actu-societes",   "label": "Boursorama"},
]

KEYWORDS_PORTEFEUILLE = [
    "orange", "bnp", "total", "capgemini", "airbus", "safran",
    "dassault", "thales", "schneider", "microsoft", "nvidia", "ge aerospace"
]
KEYWORDS_MACRO = [
    "trump", "taxe douaniere", "droits de douane", "guerre", "conflit",
    "iran", "ukraine", "russie", "chine", "fed", "bce", "taux",
    "recession", "petrole", "opep", "inflation",
    "intelligence artificielle", "chatgpt", "nvidia", "deepseek",
    "rearmement", "defense"
]

def send_telegram(message):
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        r.raise_for_status()
        print("[" + datetime.now().strftime("%H:%M") + "] Telegram OK")
    except Exception as e:
        print("[ERREUR Telegram] " + str(e))

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
    news_portfolio = []
    news_macro = []
    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:30]:
                title = entry.get("title", "")
                title_lower = title.lower()
                if any(kw in title_lower for kw in KEYWORDS_PORTEFEUILLE):
                    if title not in news_portfolio:
                        news_portfolio.append(title)
                elif any(kw in title_lower for kw in KEYWORDS_MACRO):
                    if title not in news_macro:
                        news_macro.append(title)
        except Exception as e:
            print("[ERREUR RSS] " + str(e))
    return news_portfolio[:3], news_macro[:3]

def get_sentiment(donnees):
    types_action = ["CTO", "WATCH", "WATCH-US"]
    hausses = sum(1 for d in donnees if d and d["variation"] > 0 and SEUILS.get(d["ticker"],{}).get("type") in types_action)
    baisses = sum(1 for d in donnees if d and d["variation"] < 0 and SEUILS.get(d["ticker"],{}).get("type") in types_action)
    total = hausses + baisses
    if total == 0:
        return "NEUTRE"
    ratio = hausses / total
    if ratio >= 0.65:
        return "HAUSSIER"
    elif ratio <= 0.35:
        return "BAISSIER"
    return "NEUTRE"

def calcul_pv(ticker, cours):
    """Calcule la plus-value latente sur les positions réelles"""
    s = SEUILS.get(ticker, {})
    px_revient = {
        "ORA.PA": 10.70, "CAP.PA": 161.03, "TTE.PA": 80.15,
        "BNP.PA": 85.51, "AIR.PA": 166.78, "SAF.PA": 289.87
    }
    if ticker in px_revient and s.get("quantite"):
        pv = (cours - px_revient[ticker]) * s["quantite"]
        return round(pv, 2)
    return None

def analyse_claude(donnees, moment, news_portfolio, news_macro, sentiment):
    if not ANTHROPIC_API_KEY:
        return "Cle Claude manquante dans Railway Variables."
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    lignes = []
    for d in donnees:
        if not d:
            continue
        s = SEUILS.get(d["ticker"], {})
        pv = calcul_pv(d["ticker"], d["cours"])
        pv_str = " | PV: {:+.0f}EUR".format(pv) if pv is not None else ""
        lignes.append("- {} [{}] : {}EUR ({}{}%){}" .format(
            s.get("nom", d["ticker"]), s.get("type",""),
            d["cours"], "+" if d["variation"]>=0 else "", d["variation"], pv_str))

    prompt = """Tu es un analyste financier rigoureux. Analyse pour Matthieu, investisseur débutant français.

PORTEFEUILLE RÉEL :
- Orange : 133 actions, px revient 10.70EUR → objectif dividende juin ~160EUR nets
- Capgemini : 2 actions, px revient 161EUR → en perte latente
- TotalEnergies : 7 actions, px revient 80.15EUR → lié au pétrole
- BNP Paribas : 5 actions, px revient 85.51EUR → sensible aux taux BCE
- Airbus : 3 actions, px revient 166.78EUR → sensible aux taxes Trump
- Safran : 2 actions, px revient 289.87EUR → défense + moteurs

RÈGLE IMPORTANTE : horizon 1 an, CTO (flat tax 30% sur plus-values), risque modéré.
PLAN : pas de nouveaux achats avant analyse approfondie. Priorité = laisser travailler.

MARCHÉS {} — {} :
{}

ACTUALITÉS IMPORTANTES :
Nos valeurs : {}
Macro/Géopolitique : {}

SENTIMENT : {}

ANALYSE (max 200 mots, sois béton sur les faits) :
1. Résumé marché du jour (1 phrase factuelle)
2. Impact sur chaque position réelle avec raison concrète
3. Signal unique : CONSERVER / RENFORCER / ALLEGER sur quelle valeur et pourquoi
4. Risque principal du moment : FAIBLE / MODÉRÉ / ÉLEVÉ

Sois honnête, pas de bullshit. Si tu ne sais pas, dis-le.""".format(
        moment.upper(),
        datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
        "\n".join(lignes),
        " | ".join(news_portfolio) if news_portfolio else "RAS",
        " | ".join(news_macro) if news_macro else "RAS",
        sentiment)

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}])
        return msg.content[0].text
    except Exception as e:
        return "[Erreur Claude : " + str(e) + "]"

def analyse_complete(moment):
    now = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
    print("\n[" + now + "] Analyse " + moment + "...")

    donnees = [get_cours(t) for t in SEUILS.keys()]
    donnees_ok = [d for d in donnees if d]
    if not donnees_ok:
        send_telegram("Marches fermes ou erreur reseau.")
        return

    news_portfolio, news_macro = get_news()
    sentiment = get_sentiment(donnees_ok)
    sent_emoji = "🟢" if sentiment == "HAUSSIER" else "🔴" if sentiment == "BAISSIER" else "🟡"

    # Sections du message
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
            if s["type"] not in types:
                continue
            f = "🟢" if d["variation"] >= 0 else "🔴"

            if s["type"] in ["INDEX", "MATIERES"]:
                bloc.append("{} <b>{}</b>  {}  {}{}%".format(
                    f, s["nom"], d["cours"],
                    "+" if d["variation"]>=0 else "", d["variation"]))
            else:
                pv = calcul_pv(d["ticker"], d["cours"])
                pv_str = "  <i>{:+.0f}€</i>".format(pv) if pv is not None else ""
                l = "{} <b>{}</b>  {}EUR  {}{}%{}".format(
                    f, s["nom"], d["cours"],
                    "+" if d["variation"]>=0 else "", d["variation"], pv_str)
                # Alertes seuils
                if s["achat"] and d["cours"] <= s["achat"]:
                    l += "\n   🎯 Zone d'achat !"
                    alertes_seuil.append("🎯 {} sous seuil achat ({})".format(s["nom"], s["achat"]))
                if s["vente"] and d["cours"] >= s["vente"]:
                    l += "\n   💰 Zone de vente !"
                    alertes_seuil.append("💰 {} au-dessus seuil vente ({})".format(s["nom"], s["vente"]))
                bloc.append(l)
        if bloc:
            lignes_msg.append("\n<b>{}</b>\n".format(titre) + "\n".join(bloc))

    emoji = "🌅" if moment == "matin" else "🌆"
    analyse = analyse_claude(donnees_ok, moment, news_portfolio, news_macro, sentiment)

    # News bloc — seulement si pertinent
    news_bloc = ""
    if news_portfolio or news_macro:
        news_bloc = "\n📰 <b>News importantes :</b>\n"
        for n in (news_portfolio + news_macro)[:3]:
            news_bloc += "• " + n[:80] + "\n"

    alertes_bloc = ""
    if alertes_seuil:
        alertes_bloc = "\n🚨 <b>Seuils franchis :</b>\n" + "\n".join(alertes_seuil) + "\n"

    msg = ("{} <b>Analyse {} — {}</b>\n"
           "{} Sentiment : <b>{}</b>\n"
           "――――――――――――――――――――――\n"
           "{}\n"
           "――――――――――――――――――――――"
           "{}"
           "{}\n"
           "――――――――――――――――――――――\n"
           "🤖 <b>Signal Claude :</b>\n{}\n"
           "――――――――――――――――――――――\n"
           "<i>Ouvre Claude.ai si besoin d'approfondir</i>").format(
        emoji, moment.upper(), now,
        sent_emoji, sentiment,
        "\n".join(lignes_msg),
        news_bloc, alertes_bloc,
        analyse)

    send_telegram(msg)
    print("[" + now + "] OK")

def check_alertes_intraday():
    """Alerte uniquement si variation > 3% — moins de bruit"""
    now = datetime.now(PARIS_TZ)
    # Heures de marché Paris uniquement
    if now.weekday() >= 5:  # Samedi/Dimanche
        return
    if now.hour < 9 or (now.hour >= 17 and now.minute >= 30):
        return

    alertes = []
    # Surveille uniquement les positions réelles + CAC40
    tickers_prioritaires = ["ORA.PA", "CAP.PA", "TTE.PA", "BNP.PA", "AIR.PA", "SAF.PA", "^FCHI"]

    for ticker in tickers_prioritaires:
        d = get_cours(ticker)
        if not d:
            continue
        if abs(d["variation"]) >= SEUIL_ALERTE_VARIATION:
            s = SEUILS.get(ticker, {})
            emoji = "📈" if d["variation"] > 0 else "📉"
            pv = calcul_pv(ticker, d["cours"])
            pv_str = " ({:+.0f}€ sur ta position)".format(pv) if pv else ""
            alertes.append("{} <b>{}</b> : {}EUR  {}{}%{}".format(
                emoji, s.get("nom", ticker), d["cours"],
                "+" if d["variation"]>=0 else "", d["variation"], pv_str))

    if alertes:
        _, news_macro = get_news()
        ctx = "\n📰 " + news_macro[0][:90] if news_macro else ""

        # Règle d'action automatique
        action = ""
        for d in [get_cours(t) for t in tickers_prioritaires if get_cours(t)]:
            if not d:
                continue
            s = SEUILS.get(d["ticker"], {})
            v = d["variation"]
            if abs(v) < SEUIL_ALERTE_VARIATION:
                continue
            if v <= -5.0:
                action = "\n⚡ <b>ACTION :</b> Baisse forte. Ne vends pas. Envoie cette alerte à Claude.ai pour analyser si c'est une opportunité de renforcement."
                break
            elif v >= 5.0 and s.get("achat") and d["cours"] <= s["achat"]:
                action = "\n⚡ <b>ACTION :</b> Zone d'achat atteinte. Envoie à Claude.ai pour valider l'entrée."
                break
            elif v >= 5.0 and s.get("vente") and d["cours"] >= s["vente"]:
                action = "\n⚡ <b>ACTION :</b> Zone de vente atteinte. Envoie à Claude.ai pour décider si tu allèges."
                break
            elif -5.0 < v < -SEUIL_ALERTE_VARIATION:
                action = "\n⚡ <b>ACTION :</b> Baisse modérée. Surveille. Pas d'action immédiate nécessaire."
                break
            elif SEUIL_ALERTE_VARIATION < v < 5.0:
                action = "\n⚡ <b>ACTION :</b> Hausse modérée. Continue de conserver."
                break

        msg = ("🚨 <b>ALERTE — " + now.strftime("%H:%M") + "</b>\n"
               "――――――――――――――――――――――\n" +
               "\n".join(alertes) +
               ctx +
               action +
               "\n――――――――――――――――――――――\n"
               "<i>Envoie ce message à Claude.ai si tu veux approfondir</i>")
        send_telegram(msg)

def analyse_matin(): analyse_complete("matin")
def analyse_soir():  analyse_complete("soir")

if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not ANTHROPIC_API_KEY:
        print("[ERREUR] Variables Railway manquantes")
        exit(1)

    print("=" * 50)
    print("  Bot Trading Matthieu v5")
    print("  Analyses : 09:00 et 17:30 (Paris)")
    print("  Alertes : toutes les 30min, variation > 3%")
    print("=" * 50)

    send_telegram(
        "🚀 <b>Bot v5 — Recalibré !</b>\n\n"
        "✅ Seuils réalistes sur tes vraies positions\n"
        "✅ Alertes toutes les 30min si variation > 3%\n"
        "✅ Plus-values latentes affichées en temps réel\n"
        "✅ Analyses 9h00 et 17h30 heure Paris\n\n"
        "Portefeuille suivi : Orange(133) · Cap(2) · Total(7) · BNP(5) · Airbus(3) · Safran(2)")

    # Heures UTC = Paris - 2h en été
    schedule.every().day.at("07:00").do(analyse_matin)   # 09:00 Paris
    schedule.every().day.at("15:30").do(analyse_soir)    # 17:30 Paris
    schedule.every(30).minutes.do(check_alertes_intraday)  # toutes les 30min

    while True:
        schedule.run_pending()
        time.sleep(30)
