import os
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from google import genai
import asyncio
from dotenv import load_dotenv

# ==========================
# CARGA DE VARIABLES DE ENTORNO
# ==========================
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RAILWAY_URL = os.getenv("RAILWAY_URL")
PORT = int(os.getenv("PORT", 8080))

print("TOKEN:", TOKEN)  # 👈 Verifica si Railway lo lee correctamente

# ==========================
# CONFIGURACIÓN DEL SISTEMA
# ==========================
SYSTEM_PROMPT = """Eres un psicólogo virtual llamado PazOhrBot. 
Tu objetivo es proporcionar apoyo, consejos de bienestar emocional y mantener una conversación empática y confidencial. 
Responde de forma cálida, reflexiva y en español. 
Mantente enfocado en el bienestar del usuario. 
Responde de forma concisa, no más de 4 oraciones - Eres de Nicaragua."""

MODEL_NAME = "gemini-1.5-flash"
client = genai.Client(api_key=GEMINI_API_KEY)

app = Flask(__name__)
user_message_count = {}

# ==========================
# FUNCIONES
# ==========================
def generate_response(prompt: str):
    full_prompt = f"{SYSTEM_PROMPT}\n\nUsuario: {prompt}\nPazOhrBot:"
    response = client.models.generate_content(model=MODEL_NAME, contents=full_prompt)
    return response.text.strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola 😊 Soy PazOhrBot, tu acompañante virtual para el bienestar emocional. "
        "Podés contarme cómo te sentís y te ayudaré a encontrar calma y equilibrio."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    text = update.message.text.strip()
    user_message_count[user_id] = user_message_count.get(user_id, 0) + 1

    reply = generate_response(text)
    await update.message.reply_text(reply)

    # Cada 4 o 5 mensajes: dar consejo adicional
    if user_message_count[user_id] % 4 == 0 or user_message_count[user_id] % 5 == 0:
        consejo = generate_response("Dame un consejo breve para manejar el estrés o mejorar el bienestar emocional.")
        await update.message.reply_text(consejo)

# ==========================
# CONFIGURAR APLICACIÓN TELEGRAM
# ==========================
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ==========================
# WEBHOOK PARA RAILWAY
# =====================
