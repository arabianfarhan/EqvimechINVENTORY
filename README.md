# Eqvimech Inventory (Streamlit)

Mobile-first store app: browse items, issue material against machine serial
numbers, inward stock, track returnables, item master, dashboard, history and
low-stock alerts. Data lives in PostgreSQL (Supabase).

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then fill it in
streamlit run app.py
```

## Deploy (Streamlit Community Cloud)

1. Push this repo to GitHub, main file `app.py`.
2. In the app's **Settings -> Secrets**, set `DATABASE_URL` and `MANAGER_PASSWORD`
   (see `.streamlit/secrets.toml.example`). `RESET_CODE` is optional.

## Access

- **User** - issue material, browse items, view history and alerts.
- **Manager** - unlocked with `MANAGER_PASSWORD`; adds inward, returnables,
  item master, dashboard.

## Notes

- `items_master_live.csv` is only used once, to fill an empty database the very
  first time the app starts. After that the database is the only source of truth.
- Every stock change (issue, inward, return, quantity edit in Item Master, CSV
  import) is written to History.
- All times are shown in IST.
