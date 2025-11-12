import os
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
from google import genai

# ==========================
# CONFIGURACIÓN DEL SISTEMA
# ==========================
SYSTEM_PROMPT = """Eres un psicólogo virtual llamado PazOhrBot. 
Tu objetivo es proporcionar apoyo, consejos de bienestar emocional y mantener una conversación empática y confidencial. 
Responde de forma cálida, reflexiva y en español. 
Mantente enfocado en el bienestar del usuario. 
Responde de forma concisa, no más de 4 oraciones - Eres de Nicaragua."""

# ==========================
# INICIALIZACIÓN
# ==========================
TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = "gemini-1.5-flash"

bot = Bot(token=TOKEN)
app = Flask(__name__)
client = genai.Client(api_key=GEMINI_API_KEY)

# Para llevar conteo de mensajes por usuario
user_message_count = {}

# ==========================
# FUNCIONES PRINCIPALES
# ==========================
def generate_response(user_text):
    prompt = f"{SYSTEM_PROMPT}\n\nUsuario: {user_text}\nPazOhrBot:"
    response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
    return response.text.strip()

def handle_message(update: Update, context):
    user_id = update.message.chat_id
    text = update.message.text.strip()

    # Contador de mensajes
    user_message_count[user_id] = user_message_count.get(user_id, 0) + 1

    # Respuesta principal del bot
    reply = generate_response(text)
    update.message.reply_text(reply)

    # Cada 4 o 5 mensajes: enviar un consejo adicional
    if user_message_count[user_id] % 4 == 0 or user_message_count[user_id] % 5 == 0:
        consejo = generate_response("Dame un consejo breve para manejar el estrés o mejorar el bienestar emocional.")
        update.message.reply_text(consejo)

def start(update: Update, context):
    update.message.reply_text(
        "Hola 😊 Soy PazOhrBot, tu acompañante virtual para el bienestar emocional. "
        "Podés contarme cómo te sentís y te ayudaré a encontrar calma y equilibrio."
    )

# ==========================
# TELEGRAM HANDLERS
# ==========================
dispatcher = Dispatcher(bot, None, workers=0)
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

# ==========================
# WEBHOOK PARA RAILWAY
# ==========================
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "OK", 200

@app.route("/")
def home():
    return "PazOhrBot está activo 🕊️", 200

# ==========================
# MAIN
# ==========================
if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8443))
    bot.delete_webhook()
    webhook_url = f"https://{os.getenv('RAILWAY_URL')}/{TOKEN}"
    bot.set_webhook(webhook_url)
    print(f"Webhook establecido en {webhook_url}")
    app.run(host="0.0.0.0", port=PORT)
