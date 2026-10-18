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

## Purchase requisitions (Manager → Purchase tab)

- **New PR**: add items (or all low-stock items in one click), set quantities, requester name,
  required-by date and priority. Warns if an item is already on an open PR.
- **Open PRs**: overdue PRs float to the top. Open one to mark it ordered (vendor, PO no.,
  rates and expected date are optional except the date), receive goods (partial deliveries stay
  pending), change the expected date with a reason, add notes, close short or cancel.
- **Receiving** adds stock automatically and writes an Inward line in History linked to the PR.
- **Proof**: every action is saved with the person's name and the database server's time in a log
  that cannot be edited or deleted. Print the PR sheet from the PR window.
- **Requests**: operators can ask for out-of-stock / low items from Pick or Items; managers turn
  them into a PR or dismiss them with a reason.
- **Register** (Excel export) and **Vendors** (on-time %, average lead days).

## Notes

- `items_master_live.csv` is only used once, to fill an empty database the very
  first time the app starts. After that the database is the only source of truth.
- Every stock change (issue, inward, return, quantity edit in Item Master, CSV
  import) is written to History.
- All times are shown in IST.
