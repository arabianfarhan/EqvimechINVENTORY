# Stock Keeper - Streamlit Inventory App

Minimal Streamlit app to manage inventory with a simple "take material" workflow.

Run locally:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Default demo users (change passwords in `app.py`):
- storemanager / manager123 (manager role)
- user1 / user123 (user role)

Deploy to Streamlit Community Cloud:
1. Create a GitHub repo and push this project.
2. In Streamlit Cloud, create a new app and connect the GitHub repo.
3. Set the main file to `app.py` and ensure `requirements.txt` is present.
