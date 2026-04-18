import streamlit as st

# Demo users — replace with secure store for production
_USERS = {
    "storemanager": {"password": "manager123", "role": "manager"},
    "user1": {"password": "user123", "role": "user"}
}

def login_form():
    st.sidebar.title("Login")
    username = st.sidebar.text_input("Username")
    password = st.sidebar.text_input("Password", type="password")
    if st.sidebar.button("Login"):
        user = _USERS.get(username)
        if user and user["password"] == password:
            st.session_state["user"] = username
            st.session_state["role"] = user["role"]
            st.sidebar.success(f"Logged in as {username}")
        else:
            st.sidebar.error("Invalid credentials")

def current_user():
    return st.session_state.get("user")
