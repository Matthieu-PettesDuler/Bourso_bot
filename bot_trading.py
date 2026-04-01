#!/usr/bin/env python3
import os, yfinance as yf, requests, anthropic, schedule, time, feedparser
from datetime import datetime
import pytz

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "8719135314:AAHkO4SsYqFcCUjzgNQ223eYBhUd0p5aySU")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", 7654102743)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "sk-ant-api03-X_7NyWc_hGPKA99enw_sg2ahBxtMASxfIFM411G85d-s-2Dftkj0yB_xW6h06jcgLKV3kgwDo0hmsNEKdZ5nOA-ihxBIwAA")

SEUILS = {
    # CTO France
    "ORA.PA":  {"nom": "Orange",          "achat": 16.50, "vente": 19.00, "type": "CTO", "secteur": "Telecom"},
    "CAP.PA":  {"nom": "Capgemini",       "achat": 90.00, "vente": 115.00,"type": "CTO", "secteur": "IA/Tech"},
    "TTE.PA":  {"nom": "TotalEnergies",   "achat": 50.00, "vente": 65.00, "type": "CTO", "secteur": "Energie"},
    "BNP.PA":  {"nom": "BNP Paribas",     "achat": 55.00, "vente": 75.00, "type": "CTO", "secteur": "Banque"},
    "AM.PA":   {"nom": "Dassault Aviation","achat": 295.00,"vente": 360.00,"type": "CTO", "secteur": "Defense"},
    "HO.PA":   {"nom": "Thales",          "achat": 220.00,"vente": 270.00,"type": "CTO", "secteur": "Defense/IA"},
    "AIR.PA":  {"nom": "Airbus",          "achat": 145.00,"vente": 180.00,"type": "CTO", "secteur": "Aerospatiale"},
    "SAF.PA":  {"nom": "Safran",          "achat": 200.00,"vente": 260.00,"type": "CTO", "secteur": "Defense/Moteurs"},
    # CTO USA
    "NVDA":    {"nom": "Nvidia",          "achat": 85.00, "vente": 130.00,"type": "CTO-US", "secteur": "IA/Puces"},
    "PLTR":    {"nom": "Palantir",        "achat": 70.00, "vente": 110.00,"type": "CTO-US", "secteur": "IA/Defense"},
    "MSFT":    {"nom": "Microsoft",       "achat": 380.00,"vente": 450.00,"type": "CTO-US", "secteur": "IA/Cloud"},
    # PEA
    "CW8.PA":  {"nom": "Bourso Monde",    "achat": None,  "vente": None,  "type": "PEA",    "secteur": "ETF World"},
    "ERO.PA":  {"nom": "Bourso Europe",   "achat": None,  "vente": None,  "type": "PEA",    "secteur": "ETF Europe"},
    # Indices & Matieres
    "^FCHI":   {"nom": "CAC 40",          "achat": None,  "vente": None,  "type": "INDEX",  "secteur": "Indice"},
    "GC=F":    {"nom": "Or",              "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Refuge"},
    "CL=F":    {"nom": "Petrole WTI",     "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Energie"},
}

PARIS_TZ = pytz.timezone("Europe/Paris")

RSS_FEEDS = [
    {"url": "https://feeds.reuters.com/reuters/businessNews", "label": "Reuters Business"},
    {"url": "https://feeds.reuters.com/Reuters/worldNews",    "label": "Reuters Monde"},
    {"url": "https://www.boursorama.com/rss/actu-societes",   "label": "Boursorama"},
    {"url": "https://www.lemonde.fr/economie/rss_full.xml",   "label": "Le Monde Eco"},
]

KEYWORDS_PORTEFEUILLE = [
    "orange", "bnp", "total", "capgemini", "dassault", "thales", "airbus",
    "safran", "nvidia", "palantir", "microsoft", "rafale", "falcon"
]
KEYWORDS_MACRO = [
    "trump", "tarif", "taxe douaniere", "droits de douane", "sanctions",
    "guerre", "conflit", "ukraine", "russie", "chine", "taiwan", "iran",
    "fed", "bce", "taux", "inflation", "recession", "petrole", "opep",
    "dollar", "euro", "trade war", "tariff", "defense", "rearmement",
    "intelligence artificielle", "ai", "chatgpt", "deepseek", "mistral",
    "openai", "gemini", "llm", "gpu", "semiconductor"
]

def send_telegram(message):
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
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
    return news_portfolio[:4], news_macro[:4]

def get_sentiment(donnees):
    types_action = ["CTO", "CTO-US"]
    hausses = sum(1 for d in donnees if d and d["variation"] > 0 and SEUILS.get(d["ticker"],{}).get("type") in types_action)
    baisses = sum(1 for d in donnees if d and d["variation"] < 0 and SEUILS.get(d["ticker"],{}).get("type") in types_action)
    total = hausses + baisses
    if total == 0:
        return "NEUTRE"
    ratio = hausses / total
    if ratio >= 0.7:
        return "HAUSSIER"
    elif ratio <= 0.3:
        return "BAISSIER"
    return "NEUTRE"

def analyse_claude(donnees, moment, news_portfolio, news_macro, sentiment):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    lignes = []
    for d in donnees:
        if not d:
            continue
        s = SEUILS.get(d["ticker"], {})
        lignes.append("- {} [{}|{}] : {} ({}{}%)".format(
            s.get("nom", d["ticker"]), s.get("type",""), s.get("secteur",""),
            d["cours"], "+" if d["variation"]>=0 else "", d["variation"]))

    prompt = """Tu es un analyste financier expert. Analyse pour un investisseur débutant français.

PORTEFEUILLE :
CTO France : Orange (183 actions) + Capgemini + TotalEnergies + BNP + Dassault Aviation + Thales + Airbus + Safran
CTO USA : Nvidia + Palantir + Microsoft (en surveillance)
PEA : Bourso Monde 200EUR/mois + Bourso Europe 100EUR/mois + Or
Objectif : revenus réguliers + croissance, risque modéré, horizon 1 an

MARCHÉS {} — {} :
{}

ACTUALITÉS NOS VALEURS :
{}

ACTUALITÉS GÉOPOLITIQUES, DÉFENSE & IA :
{}

SENTIMENT : {}

ANALYSE GÉOPOLITIQUE APPROFONDIE — fais les liens :
- Réarmement Europe → Dassault, Thales, Safran, Airbus
- Révolution IA (ChatGPT, Nvidia, DeepSeek...) → Capgemini, Thales, Palantir, Nvidia, Microsoft
- Taxes Trump / guerre commerciale → TotalEnergies, Airbus, Nvidia (puces)
- Taux BCE → BNP Paribas, Orange
- Conflits → Or (refuge) + Pétrole + Dassault

FORMAT DE RÉPONSE (max 250 mots) :
1. Résumé marché + contexte géopolitique du jour (2 phrases)
2. TOP 3 signaux du jour avec raison géopolitique/IA
3. Action prioritaire concrète (1 seule)
4. Risque global : FAIBLE/MODÉRÉ/ÉLEVÉ + raison

Réponds en français, sois direct et pédagogique pour un débutant.""".format(
        moment.upper(),
        datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
        "\n".join(lignes),
        "\n".join(["• " + n for n in news_portfolio]) if news_portfolio else "Aucune",
        "\n".join(["• " + n for n in news_macro]) if news_macro else "Aucune",
        sentiment)

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
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
        send_telegram("Impossible de recuperer les cours (marches fermes ?).")
        return

    news_portfolio, news_macro = get_news()
    sentiment = get_sentiment(donnees_ok)
    sent_emoji = "🟢" if sentiment == "HAUSSIER" else "🔴" if sentiment == "BAISSIER" else "🟡"

    # Bloc cours par catégorie
    sections = [
        ("📊 CAC + Marchés", ["INDEX", "MATIERES"]),
        ("🏦 CTO France", ["CTO"]),
        ("🌍 CTO USA", ["CTO-US"]),
        ("📈 PEA", ["PEA"]),
    ]

    lignes_msg = []
    alertes = []
    for titre, types in sections:
        bloc = []
        for d in donnees_ok:
            s = SEUILS[d["ticker"]]
            if s["type"] not in types:
                continue
            f = "🟢" if d["variation"] >= 0 else "🔴"
            if s["type"] in ["INDEX", "MATIERES"]:
                bloc.append("{} <b>{}</b>  {}  {}{}%".format(
                    f, s["nom"], d["cours"], "+" if d["variation"]>=0 else "", d["variation"]))
            else:
                l = "{} <b>{}</b> [{}]  {}  {}{}%".format(
                    f, s["nom"], s["secteur"], d["cours"],
                    "+" if d["variation"]>=0 else "", d["variation"])
                if s["achat"] and d["cours"] <= s["achat"]:
                    l += "\n   ⚡ Seuil achat {}".format(s["achat"])
                    alertes.append("⚡ {} sous seuil achat !".format(s["nom"]))
                if s["vente"] and d["cours"] >= s["vente"]:
                    l += "\n   ⚡ Seuil vente {}".format(s["vente"])
                    alertes.append("⚡ {} au-dessus seuil vente !".format(s["nom"]))
                bloc.append(l)
        if bloc:
            lignes_msg.append("\n<b>{}</b>\n".format(titre) + "\n".join(bloc))

    emoji = "🌅" if moment == "matin" else "🌆"
    analyse = analyse_claude(donnees_ok, moment, news_portfolio, news_macro, sentiment)

    news_bloc = ""
    if news_portfolio or news_macro:
        news_bloc = "\n"
        if news_portfolio:
            news_bloc += "📊 <b>Nos valeurs :</b>\n" + "\n".join(["• " + n[:70] for n in news_portfolio[:3]]) + "\n"
        if news_macro:
            news_bloc += "🌍 <b>Défense/IA/Géopolitique :</b>\n" + "\n".join(["• " + n[:70] for n in news_macro[:3]]) + "\n"

    alertes_bloc = "\n🚨 " + " | ".join(alertes) + "\n" if alertes else ""

    msg = ("{} <b>Analyse {} — {}</b>\n"
           "{} Sentiment : <b>{}</b>\n"
           "――――――――――――――――――――――\n"
           "{}\n"
           "――――――――――――――――――――――"
           "{}{}\n"
           "――――――――――――――――――――――\n"
           "🤖 <b>Analyse géopolitique & IA :</b>\n{}\n"
           "――――――――――――――――――――――\n"
           "<i>Réponds ici ou ouvre Claude.ai</i>").format(
        emoji, moment.upper(), now,
        sent_emoji, sentiment,
        "\n".join(lignes_msg),
        news_bloc, alertes_bloc,
        analyse)

    send_telegram(msg)
    print("[" + now + "] OK")

def check_alertes_intraday():
    now = datetime.now(PARIS_TZ)
    if now.hour < 9 or (now.hour >= 17 and now.minute >= 30):
        return
    alertes = []
    for ticker in SEUILS.keys():
        if SEUILS[ticker]["type"] in ["INDEX", "MATIERES", "PEA"]:
            continue
        d = get_cours(ticker)
        if not d:
            continue
        if abs(d["variation"]) >= 2.0:
            s = SEUILS[ticker]
            emoji = "📈" if d["variation"] > 0 else "📉"
            alertes.append("{} <b>{}</b> [{}] : {}  {}{}%".format(
                emoji, s["nom"], s["secteur"], d["cours"],
                "+" if d["variation"]>=0 else "", d["variation"]))
    if alertes:
        _, news_macro = get_news()
        ctx = "\n🌍 Contexte : " + news_macro[0][:80] if news_macro else ""
        msg = ("🚨 <b>ALERTE — " + datetime.now(PARIS_TZ).strftime("%H:%M") + "</b>\n"
               "――――――――――――――――――――――\n" +
               "\n".join(alertes) + ctx +
               "\n――――――――――――――――――――――\n"
               "<i>Ouvre Claude.ai pour analyse</i>")
        send_telegram(msg)

def analyse_matin(): analyse_complete("matin")
def analyse_soir():  analyse_complete("soir")

if __name__ == "__main__":
    print("=" * 50)
    print("  Bot Trading Boursobank v4 — Railway")
    print("  Défense + IA + Géopolitique")
    print("  09:00 et 17:30 + alertes intraday")
    print("=" * 50)
    send_telegram(
        "🚀 <b>Bot Trading v4 — Mise à jour !</b>\n\n"
        "Nouvelles valeurs surveillées :\n"
        "🛡 <b>Défense FR :</b> Dassault, Thales, Airbus, Safran\n"
        "🤖 <b>IA US :</b> Nvidia, Palantir, Microsoft\n"
        "📡 <b>Liens géopolitiques :</b> Réarmement Europe, IA, Trump taxes\n\n"
        "Analyses à 9h00 et 17h30 🌍")
    schedule.every().day.at("09:00").do(analyse_matin)
    schedule.every().day.at("17:30").do(analyse_soir)
    schedule.every(30).minutes.do(check_alertes_intraday)
    while True:
        schedule.run_pending()
        time.sleep(30)
