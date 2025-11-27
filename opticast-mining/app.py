import streamlit as st
import sys
import subprocess
import os

st.set_page_config(page_title="Modo Diagnóstico", page_icon="🔧")

st.title("🔧 Modo Diagnóstico de OptiCast")

# 1. Verificación de Python
st.subheader("1. Versión de Python")
st.write(sys.version)

# 2. Verificación de Librería y Reparación Forzada
st.subheader("2. Estado de google-generativeai")

try:
    import google.generativeai as genai
    version_actual = genai.__version__
    st.write(f"Versión cargada actualmente: **{version_actual}**")
    
    # Si la versión es vieja, intentamos forzar la actualización aquí mismo
    if version_actual < "0.7.2":
        st.error("⚠️ Versión obsoleta detectada. Intentando actualizar forzosamente...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "google-generativeai"])
            st.success("✅ Librería actualizada. POR FAVOR REINICIA LA APP (Reboot).")
            st.stop() # Detenemos la ejecución para pedir reinicio
        except Exception as e:
            st.error(f"No se pudo actualizar automáticamente: {e}")

except ImportError:
    st.error("❌ La librería google-generativeai NO está instalada.")
    st.stop()

# 3. Prueba de Conexión y Listado de Modelos
st.subheader("3. Prueba de Conexión con API Key")

# Intentamos obtener la key
api_key = st.secrets.get("GEMINI_API_KEY")

if not api_key:
    st.error("❌ No se detectó la API Key en st.secrets.")
    api_key = st.text_input("Ingresa tu API Key manual para probar:")

if api_key:
    genai.configure(api_key=api_key)
    
    try:
        st.write("Intentando listar modelos disponibles para tu API Key...")
        modelos = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                modelos.append(m.name)
        
        st.success(f"✅ Conexión exitosa. Se encontraron {len(modelos)} modelos.")
        st.write("Modelos disponibles:", modelos)
        
        # Verificación específica del modelo Flash
        if 'models/gemini-1.5-flash' in modelos:
            st.balloons()
            st.success("✨ ¡CONFIRMADO! 'models/gemini-1.5-flash' está disponible y listo para usarse.")
            st.info("Ahora puedes volver a poner tu código original de OptiCast.")
        else:
            st.warning("⚠️ La conexión funciona, pero NO veo 'gemini-1.5-flash' en la lista. ¿Tu API Key tiene acceso a este modelo?")
            
    except Exception as e:
        st.error(f"❌ Error conectando con Google: {e}")