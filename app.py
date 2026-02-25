import streamlit as st
from core.viabilidade_res_unifamiliar import render_unifamiliar

st.set_page_config(layout="wide", page_title="Viabilidade")
render_unifamiliar()
