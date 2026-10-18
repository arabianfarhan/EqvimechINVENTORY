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

## Searching

Search is forgiving everywhere: spaces, dashes and word order don't matter, and letters/numbers can
be typed together — `m10 x16`, `bolt 10x16`, `r25ballscrew` and `100 kn` all find the right items.
Small spelling slips (`timing` vs `TIMMING`) are tolerated too.

## Item Master editing

Edit as many cells as you like, then press **Save changes** once (fast, nothing gets lost).
Turn on "Save each edit immediately" if you prefer the old behaviour.

## Duplicate items (Master → Duplicate items)

The app spots items that are probably the same part entered twice (same size and near-identical
name, e.g. "10x16 ALLEN Bolt HT" and "M10x16 Allen Bolt"). For each group: pick the item to keep, enter
the real counted stock, and press **Merge**. History, PRs and requests move to the kept item; the
duplicate is switched off (not deleted); every merge is logged with name and time. "Not duplicates"
hides a wrong suggestion for good. Adding a new item that looks like an existing one shows a warning.

## Purchase requisitions (Manager → Purchase tab)

- **New PR**: search and tap **Add**, or add all low-stock / out-of-stock items / floor requests in one
  tap. Adjust each line with − / ＋ or remove it. Requester is remembered; pick the required-by date with
  one tap (Today, 3 days, 1 week…). Warns if an item is already on an open PR.
- **Open PRs**: overdue PRs float to the top. Open one to mark it ordered (vendor, PO no.,
  rates and expected date are optional except the date), receive goods (partial deliveries stay
  pending), change the expected date with a reason, add notes, close short or cancel.
- **Receiving** adds stock automatically and writes an Inward line in History linked to the PR.
- **Proof**: every action is saved with the person's name and the database server's time in a log
  that cannot be edited or deleted. Print the PR sheet from the PR window.
- **Requests**: operators can ask for out-of-stock / low items from Pick or Items; managers turn
  them into a PR or dismiss them with a reason.
- **Pending items**: every item still awaited across all open PRs, overdue first, with vendor,
  PO, due date and days open. Filter, export to Excel, or share on WhatsApp.
- **Register** (Excel export) and **Vendors** (on-time %, average lead days).
- **Share on WhatsApp**: from a PR, the pending list or Alerts. It opens WhatsApp with the message
  ready; pick your group and send. (WhatsApp's official API cannot post into an existing group,
  so this uses WhatsApp's own share link — free, no setup.)

## Notes

- `items_master_live.csv` is only used once, to fill an empty database the very
  first time the app starts. After that the database is the only source of truth.
- Every stock change (issue, inward, return, quantity edit in Item Master, CSV
  import) is written to History.
- All times are shown in IST.
