import streamlit as st
import requests
import base64
import os
from dotenv import load_dotenv

load_dotenv()

def get_auth_credentials():
    """Get authentication credentials"""
    username = os.getenv("APP_USERNAME")
    password = os.getenv("APP_PASSWORD")
    return username, password

def create_auth_header(username, password):
    """Create HTTP Basic Auth header"""
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {credentials}"}

def check_authentication():
    """Check if user is authenticated"""
    if 'authenticated' not in st.session_state:
        st.session_state.authenticated = False
    
    if not st.session_state.authenticated:
        show_login_form()
        return False
    
    return True

def show_login_form():
    """Show login form"""
    st.title("🔐 Medical Case Generator - Authentication Required")
    st.markdown("Please enter your credentials to access the application.")
    
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login")
        
        if submit:
            correct_username, correct_password = get_auth_credentials()
            
            if username == correct_username and password == correct_password:
                st.session_state.authenticated = True
                st.session_state.auth_header = create_auth_header(username, password)
                st.success("Authentication successful!")
                st.rerun()
            else:
                st.error("Invalid credentials. Please try again.")

def get_auth_header():
    """Get auth header for API requests"""
    if 'auth_header' in st.session_state:
        return st.session_state.auth_header
    return {}

def logout():
    """Logout user"""
    st.session_state.authenticated = False
    if 'auth_header' in st.session_state:
        del st.session_state.auth_header
    st.rerun()