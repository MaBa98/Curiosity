# Curiosity

Feed verticale di curiosità (Finanza, Storia, Scienza) tratte da Wikipedia.
Single-file vanilla (`index.html`), dark glassmorphism, mobile-first.

## Guida rapida

```bash
pip install requests
python scraper_etl.py --batch-size 20 --lang it   # aggiorna curiosities_db.json
python -m http.server 8000                        # poi apri http://localhost:8000
```

- `scraper_etl.py` legge le sotto-categorie (categorymembers), poi estratti e REST `/page/summary/`.
  Scarta estratti < 350 caratteri, stub e disambiguazioni; deduplica per `pageid`.
- `curiosities_db.json`: `{version, updated_at, items[...]}`.
- `index.html` carica il JSON; se non disponibile (es. apertura da `file://`) usa 11 curiosità integrate.
- Funzioni: scroll-snap, filtro categoria, Shuffle, drawer "Approfondisci", "Fonte Ufficiale", preferiti (localStorage).

## Aggiornamento automatico

`.github/workflows/update_curiosities.yml` gira ogni giorno (e manualmente da **Actions → Update curiosities → Run workflow**)
e committa il JSON solo se cambia.

## Attivare GitHub Pages

1. **Settings → Pages**
2. *Source*: **Deploy from a branch**
3. *Branch*: `main`, cartella `/ (root)` → **Save**
4. Dopo circa un minuto il sito è su `https://<utente>.github.io/Curiosity/`

Se **Settings → Actions → General → Workflow permissions** è in sola lettura, il workflow dichiara già `contents: write`.
