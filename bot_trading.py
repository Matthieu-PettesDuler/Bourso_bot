#!/usr/bin/env python3
import os, yfinance as yf, requests, anthropic, schedule, time, feedparser
from datetime import datetime
import pytz

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "8719135314:AAHkO4SsYqFcCUjzgNQ223eYBhUd0p5aySU")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", 7654102743)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "sk-ant-api03-X_7NyWc_hGPKA99enw_sg2ahBxtMASxfIFM411G85d-s-2Dftkj0yB_xW6h06jcgLKV3kgwDo0hmsNEKdZ5nOA-ihxBIwAA")

SEUILS = {
    "ORA.PA":  {"nom": "Orange",       "achat": 16.50, "vente": 19.00, "type": "CTO"},
    "CAP.PA":  {"nom": "Capgemini",    "achat": 90.00, "vente": 115.00,"type": "CTO"},
    "TTE.PA":  {"nom": "TotalEnergies","achat": 50.00, "vente": 65.00, "type": "CTO"},
    "BNP.PA":  {"nom": "BNP Paribas",  "achat": 55.00, "vente": 75.00, "type": "CTO"},
    "CW8.PA":  {"nom": "Bourso Monde", "achat": None,  "vente": None,  "type": "PEA"},
    "ERO.PA":  {"nom": "Bourso Europe","achat": None,  "vente": None,  "type": "PEA"},
    "^FCHI":   {"nom": "CAC 40",       "achat": None,  "vente": None,  "type": "INDEX"},
    "GC=F":    {"nom": "Or",           "achat": None,  "vente": None,  "type": "MATIERES"},
    "CL=F":    {"nom": "Petrole WTI",  "achat": None,  "vente": None,  "type": "MATIERES"},
}

PARIS_TZ = pytz.timezone("Europe/Paris")

# Flux RSS — actualités financières ET géopolitiques
RSS_FEEDS = [
    {"url": "https://feeds.reuters.com/reuters/businessNews",   "label": "Reuters Business"},
    {"url": "https://feeds.reuters.com/Reuters/worldNews",      "label": "Reuters Monde"},
    {"url": "https://www.boursorama.com/rss/actu-societes",     "label": "Boursorama"},
    {"url": "https://www.lemonde.fr/economie/rss_full.xml",     "label": "Le Monde Eco"},
]

# Mots-clés par thème
KEYWORDS_PORTEFEUILLE = ["orange", "bnp", "total", "capgemini", "cac 40", "euronext"]
KEYWORDS_MACRO = [
    "trump", "tarif", "taxe douanière", "droits de douane", "sanctions",
    "guerre", "conflit", "ukraine", "russie", "chine", "taiwan",
    "fed", "bce", "taux", "inflation", "récession", "pétrole", "opep",
    "dollar", "euro", "yuan", "trade war", "tariff"
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
    news_portefeuille = []
    news_macro = []
    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:30]:
                title = entry.get("title", "")
                title_lower = title.lower()
                if any(kw in title_lower for kw in KEYWORDS_PORTEFEUILLE):
                    if title not in news_portefeuille:
                        news_portefeuille.append(title)
                elif any(kw in title_lower for kw in KEYWORDS_MACRO):
                    if title not in news_macro:
                        news_macro.append(title)
        except Exception as e:
            print("[ERREUR RSS " + feed_info["label"] + "] " + str(e))
    return news_portefeuille[:4], news_macro[:4]

def get_sentiment(donnees):
    hausses = sum(1 for d in donnees if d and d["variation"] > 0 and SEUILS.get(d["ticker"],{}).get("type") not in ["INDEX","MATIERES"])
    baisses = sum(1 for d in donnees if d and d["variation"] < 0 and SEUILS.get(d["ticker"],{}).get("type") not in ["INDEX","MATIERES"])
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

    lignes_marche = []
    for d in donnees:
        if not d:
            continue
        s = SEUILS.get(d["ticker"], {})
        lignes_marche.append("- {} ({}) [{}] : {}EUR/pts ({}{}%)".format(
            s.get("nom", d["ticker"]), d["ticker"], s.get("type",""),
            d["cours"], "+" if d["variation"]>=0 else "", d["variation"]))

    actu_portfolio = "\n".join(["• " + n for n in news_portfolio]) if news_portfolio else "Aucune"
    actu_macro = "\n".join(["• " + n for n in news_macro]) if news_macro else "Aucune"

    prompt = """Tu es un analyste financier expert. Analyse la situation pour un investisseur débutant français.

PORTEFEUILLE :
- CTO : Orange (183 actions, px 10.70EUR) + Capgemini (2 actions) + TotalEnergies + BNP Paribas
- PEA : Bourso Monde 200EUR/mois + Bourso Europe 100EUR/mois
- Or en surveillance
- Objectif : revenus réguliers, risque minimal, horizon 1 an
- CTO : flat tax 30% sur plus-values

MARCHÉS {} — {} :
{}

ACTUALITÉS SUR NOS VALEURS :
{}

ACTUALITÉS GÉOPOLITIQUES & MACRO :
{}

SENTIMENT GÉNÉRAL : {}

MISSION CRITIQUE — fais les LIENS entre géopolitique et portefeuille :
1. Résumé marché en 1 phrase
2. Impact géopolitique/macro sur chaque valeur :
   - Taxes douanières Trump → impact sur TotalEnergies (pétrole), Capgemini (tech US)
   - Conflits → impact sur le prix du pétrole → TotalEnergies
   - Taux BCE → impact sur BNP Paribas
   - Guerre/tensions → impact sur l'or et les valeurs refuges
3. Signal pour chaque valeur : ACHETER/RENFORCER/CONSERVER/ALLEGER/VENDRE + raison géopolitique si applicable
4. Action prioritaire du jour (1 seule, concrète)
5. Risque global : FAIBLE/MODÉRÉ/ÉLEVÉ + raison principale

Réponds en français, max 220 mots, sois direct et pédagogique.""".format(
        moment.upper(),
        datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
        "\n".join(lignes_marche),
        actu_portfolio,
        actu_macro,
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

    # Construction du bloc cours
    lignes = []
    alertes = []
    for d in donnees_ok:
        s = SEUILS[d["ticker"]]
        f = "🟢" if d["variation"] >= 0 else "🔴"
        if s["type"] == "INDEX":
            lignes.append("{} <b>{}</b>   {:,.0f}pts  {}{}%".format(
                f, s["nom"], d["cours"], "+" if d["variation"]>=0 else "", d["variation"]))
        elif s["type"] == "MATIERES":
            lignes.append("{} <b>{}</b>   {}$  {}{}%".format(
                f, s["nom"], d["cours"], "+" if d["variation"]>=0 else "", d["variation"]))
        else:
            l = "{} <b>{}</b> [{}]   {}EUR  {}{}%".format(
                f, s["nom"], s["type"], d["cours"],
                "+" if d["variation"]>=0 else "", d["variation"])
            if s["achat"] and d["cours"] <= s["achat"]:
                l += "\n   ⚡ Sous seuil achat {}EUR".format(s["achat"])
                alertes.append("⚡ {} sous seuil achat !".format(s["nom"]))
            if s["vente"] and d["cours"] >= s["vente"]:
                l += "\n   ⚡ Au-dessus seuil vente {}EUR".format(s["vente"])
                alertes.append("⚡ {} au-dessus seuil vente !".format(s["nom"]))
            lignes.append(l)

    emoji = "🌅" if moment == "matin" else "🌆"
    analyse = analyse_claude(donnees_ok, moment, news_portfolio, news_macro, sentiment)

    # Bloc actualités
    news_bloc = ""
    if news_portfolio or news_macro:
        news_bloc = "\n"
        if news_portfolio:
            news_bloc += "📊 <b>Nos valeurs :</b>\n" + "\n".join(["• " + n[:75] for n in news_portfolio[:3]]) + "\n"
        if news_macro:
            news_bloc += "🌍 <b>Géopolitique :</b>\n" + "\n".join(["• " + n[:75] for n in news_macro[:3]]) + "\n"

    alertes_bloc = "\n🚨 <b>Alertes prix :</b>\n" + "\n".join(alertes) + "\n" if alertes else ""

    msg = ("{} <b>Analyse {} — {}</b>\n"
           "{} Sentiment : <b>{}</b>\n"
           "――――――――――――――――――――――\n"
           "{}\n"
           "――――――――――――――――――――――"
           "{}"
           "{}\n"
           "――――――――――――――――――――――\n"
           "🤖 <b>Analyse géopolitique & signal :</b>\n{}\n"
           "――――――――――――――――――――――\n"
           "<i>Ouvre Claude.ai pour approfondir</i>").format(
        emoji, moment.upper(), now,
        sent_emoji, sentiment,
        "\n".join(lignes),
        news_bloc,
        alertes_bloc,
        analyse)

    send_telegram(msg)
    print("[" + now + "] Analyse envoyee")

def check_alertes_intraday():
    now = datetime.now(PARIS_TZ)
    if now.hour < 9 or (now.hour >= 17 and now.minute >= 30):
        return
    tickers = ["ORA.PA", "CAP.PA", "TTE.PA", "BNP.PA", "^FCHI", "GC=F"]
    alertes = []
    for ticker in tickers:
        d = get_cours(ticker)
        if not d:
            continue
        if abs(d["variation"]) >= 2.0:
            s = SEUILS.get(ticker, {})
            emoji = "📈" if d["variation"] > 0 else "📉"
            alertes.append("{} <b>{}</b> : {}  {}{}%".format(
                emoji, s.get("nom", ticker), d["cours"],
                "+" if d["variation"]>=0 else "", d["variation"]))
    if alertes:
        # Cherche la news macro associée
        _, news_macro = get_news()
        news_txt = "\n🌍 Contexte : " + news_macro[0][:80] if news_macro else ""
        msg = ("🚨 <b>ALERTE INTRADAY — " + datetime.now(PARIS_TZ).strftime("%H:%M") + "</b>\n"
               "――――――――――――――――――――――\n" +
               "\n".join(alertes) +
               news_txt +
               "\n――――――――――――――――――――――\n"
               "<i>Ouvre Claude.ai pour analyse immédiate</i>")
        send_telegram(msg)

def analyse_matin(): analyse_complete("matin")
def analyse_soir():  analyse_complete("soir")

if __name__ == "__main__":
    print("=" * 50)
    print("  Bot Trading Boursobank v3 — Railway")
    print("  Analyses géopolitiques : 09:00 et 17:30")
    print("  Alertes intraday : variation > 2%")
    print("=" * 50)
    send_telegram(
        "🚀 <b>Bot Trading v3 demarre !</b>\n\n"
        "Nouveau : Analyse géopolitique intégrée\n"
        "Liens automatiques entre :\n"
        "• Taxes douanières Trump → TotalEnergies, Capgemini\n"
        "• Conflits → Pétrole → Or\n"
        "• Taux BCE → BNP Paribas\n"
        "• Tensions → Valeurs refuges\n\n"
        "Analyses à 9h00 et 17h30 🌍")
    schedule.every().day.at("09:00").do(analyse_matin)
    schedule.every().day.at("17:30").do(analyse_soir)
    schedule.every(30).minutes.do(check_alertes_intraday)
    while True:
        schedule.run_pending()
        time.sleep(30)
