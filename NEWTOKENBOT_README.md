# SWOGE FUN — Bot Telegram "Nouveau Token" 🚀

Un bot qui **poste dans ton groupe Telegram à chaque nouveau token lancé** sur ton
launchpad — avec le **nom, symbol, créateur, contrat, le lien DexScreener et le logo
en photo**. Il ne fait que **lire** la blockchain (zéro risque, aucune clé privée).

Runs anywhere (Railway, VPS, ton PC). **Aucune dépendance** (Python standard).

---

## Ce que ça affiche

```
🚀 NEW TOKEN LAUNCHED on SWOGE FUN
[photo du logo]

Only Green  ($OG)
🔒 LP Locked · ⚡ live on Uniswap

👤 Creator: 0x8126…40ae
📄 Contract: 0xd7d3…7031
📊 DexScreener · 🛒 Buy on SWOGE · 🔎 Explorer
```

---

## Étape 1 — Créer le bot Telegram
1. Sur Telegram, ouvre **@BotFather** → `/newbot` → choisis un nom + @username.
2. Récupère le **token** (`123456:ABC-DEF…`).

## Étape 2 — ID de ton groupe
1. Ajoute le bot dans ton groupe et mets-le **admin** (pour qu'il puisse écrire).
2. Ajoute **@RawDataBot** une minute → il affiche `"chat":{"id": -100…}` → c'est ton **CHAT_ID** (commence souvent par `-100`). Retire ensuite RawDataBot.

## Étape 3 — Déployer sur Railway (gratuit, 24/7)
1. **railway.app** → New Project → Deploy from your GitHub repo.
2. Renomme `Procfile.newtokenbot` en **`Procfile`** (contenu : `worker: python newtokenbot.py`).
   Si Railway ne détecte pas Python, renomme aussi `requirements-newtokenbot.txt` en **`requirements.txt`**.
3. Dans **Variables**, ajoute :

| Variable | Valeur |
|---|---|
| `TELEGRAM_BOT_TOKEN` | le token de BotFather |
| `TELEGRAM_CHAT_ID` | l'id du groupe (ex : `-100123456789`) |
| `SITE_URL` | *(optionnel)* `https://swoleeswoge.dog/launchpad.html` |
| `POLL_SECONDS` | *(optionnel)* fréquence de vérif, défaut `20` |
| `ANNOUNCE_BACKLOG` | *(optionnel)* `1` pour poster les derniers tokens déjà lancés au démarrage (défaut `0` = seulement les nouveaux) |

4. Deploy → tu dois voir `Ready. Watching from block …` dans les logs.
5. Lance un token de test sur le site → le bot poste dans le groupe 🎉

---

## Tester en local (optionnel)
```bash
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="-100..."
python3 newtokenbot.py
```
Sans `TELEGRAM_BOT_TOKEN`, le bot tourne en **dry-run** : il imprime les messages dans le terminal au lieu de les envoyer.

---

## Notes
- **Aucune clé privée**, **aucune dépendance** — il ne fait que lire la blockchain.
- Le **logo** du token (stocké on-chain) est envoyé en photo automatiquement. Si un
  token n'a pas de logo, le bot poste juste le texte.
- Par défaut il n'annonce que les tokens lancés **après** son démarrage. Mets
  `ANNOUNCE_BACKLOG=1` pour aussi poster les 5 derniers au lancement.
- Contrat surveillé : `0x4De26D120A4fF2d7c1875E6C7D611262b9cA426d` (SwogeFun V2).
