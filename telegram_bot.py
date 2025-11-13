import os
import asyncio
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from google import genai
from dotenv import load_dotenv

# ==========================
# CONFIGURACIÓN INICIAL
# ==========================
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RAILWAY_URL = os.getenv("RAILWAY_URL")
PORT = int(os.getenv("PORT", 8080))

# Validación de variables
print(f"✅ TOKEN cargado: {'Sí' if TOKEN else 'No'}")
print(f"✅ GEMINI_API_KEY cargada: {'Sí' if GEMINI_API_KEY else 'No'}")
print(f"✅ RAILWAY_URL: {RAILWAY_URL}")

# Configurar logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==========================
# CONFIGURACIÓN DEL MODELO
# ==========================
SYSTEM_PROMPT = """Eres un psicólogo virtual llamado PazOhrBot. 
Tu objetivo es proporcionar apoyo, consejos de bienestar emocional y mantener una conversación empática y confidencial. 
Responde de forma cálida, reflexiva y en español. 
Mantente enfocado en el bienestar del usuario. 
Responde de forma concisa, no más de 4 oraciones - Eres de Nicaragua.
"""

MODEL_NAME = "gemini-1.5-flash"
client = genai.Client(api_key=GEMINI_API_KEY)

# Flask App
app = Flask(__name__)

# Contador de mensajes por usuario
user_message_count = {}

# ==========================
# FUNCIÓN IA
# ==========================
def generate_response(prompt: str) -> str:
    """Genera una respuesta usando Google Gemini."""
    try:
        full_prompt = f"{SYSTEM_PROMPT}\n\nUsuario: {prompt}\nPazOhrBot:"
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=full_prompt
        )
        return response.text.strip() if response and response.text else "🤖 No pude generar respuesta, intentá de nuevo."
    except Exception as e:
        logger.error(f"Error al conectar con Gemini: {e}")
        return "😔 Lo siento, hubo un error al procesar tu mensaje. Intentá nuevamente más tarde."

# ==========================
# MANEJADORES TELEGRAM
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola 😊 Soy PazOhrBot, tu acompañante virtual para el bienestar emocional. "
        "Podés contarme cómo te sentís y te ayudaré a encontrar calma y equilibrio."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    text = update.message.text.strip()

    user_message_count[user_id] = user_message_count.get(user_id, 0) + 1

    logger.info(f"Mensaje recibido de {user_id}: {text}")

    reply = generate_response(text)
    await update.message.reply_text(reply)

    # Cada 4 o 5 mensajes, enviar un consejo adicional
    if user_message_count[user_id] % 4 == 0 or user_message_count[user_id] % 5 == 0:
        consejo = generate_response("Dame un consejo breve para manejar el estrés o mejorar el bienestar emocional.")
        await update.message.reply_text(consejo)

# ==========================
# CONFIGURAR TELEGRAM APP
# ==========================
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ==========================
# WEBHOOK RAILWAY
# ==========================
@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook():
    """Recibe actualizaciones desde Telegram y las envía al bot."""
    json_update = request.get_json(force=True)
    update = Update.de_json(json_update, application.bot)
    await application.process_update(update)
    return "OK", 200

@app.route("/")
def home():
    return "🕊️ PazOhrBot está activo en Railway", 200

# ==========================
# INICIO DEL SERVICIO
# ==========================
async def main():
    await application.initialize()

    # Asegurar https:// en la URL del webhook
    base_url = RAILWAY_URL
    if not base_url.startswith("https://"):
        base_url = f"https://{base_url}"

    webhook_url = f"{base_url}/{TOKEN}"

    await application.bot.set_webhook(webhook_url)
    logger.info(f"✅ Webhook establecido correctamente en: {webhook_url}")

if __name__ == "__main__":
    asyncio.run(main())
    app.run(host="0.0.0.0", port=PORT)
