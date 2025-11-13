import os
import asyncio
import concurrent.futures
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from google import genai
from dotenv import load_dotenv
import logging

# Configuración de logs para ver más detalles en Railway
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================
# CARGA DE VARIABLES DE ENTORNO
# ==========================
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RAILWAY_URL = os.getenv("RAILWAY_URL")
PORT = int(os.getenv("PORT", 8080))

# Verificar configuración
if not all([TOKEN, GEMINI_API_KEY, RAILWAY_URL]):
    logger.error("🚨 FALTA UNA VARIABLE DE ENTORNO CRÍTICA (TELEGRAM_TOKEN, GEMINI_API_KEY, o RAILWAY_URL).")
    exit(1)
else:
    RAILWAY_URL = RAILWAY_URL.replace("https://", "")
    logger.info(f"✅ Variables cargadas. Webhook URL base: https://{RAILWAY_URL}/{TOKEN}")

# ==========================
# CONFIGURACIÓN DEL SISTEMA Y CLIENTE GEMINI
# ==========================
SYSTEM_PROMPT = """Eres un psicólogo virtual llamado PazOhrBot.
Tu objetivo es proporcionar apoyo, consejos de bienestar emocional y mantener una conversación empática y confidencial.
Responde de forma cálida, reflexiva y en español.
Mantente enfocado en el bienestar del usuario.
Responde de forma concisa, no más de 4 oraciones - Eres de Nicaragua."""

MODEL_NAME = "gemini-1.5-flash"
client = genai.Client(api_key=GEMINI_API_KEY)
executor = concurrent.futures.ThreadPoolExecutor(max_workers=5) # Ejecutor para tareas en segundo plano

# ==========================
# FUNCIONES (Ahora síncronas, se ejecutan en el ThreadPool)
# ==========================
def generate_response_sync(prompt: str):
    """
    Genera una respuesta de Gemini de forma SÍNCRONA. 
    Esta función se llamará dentro de un ThreadPool.
    """
    full_prompt = f"{SYSTEM_PROMPT}\n\nUsuario: {prompt}\nPazOhrBot:"
    
    try:
        # La llamada es síncrona
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=full_prompt
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error al llamar a Gemini: {e}")
        return "Disculpá, tuve un pequeño fallo técnico. ¿Podrías repetirme lo que me decías? 🙏"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /start."""
    await update.message.reply_text(
        "¡Hola 😊! Soy PazOhrBot, tu acompañante virtual para el bienestar emocional. "
        "Podés contarme cómo te sentís y te ayudaré a encontrar calma y equilibrio."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja mensajes de texto. La generación de Gemini se hace 
    en un hilo (síncrona), y el envío de Telegram es asíncrono.
    """
    
    if 'message_count' not in context.user_data:
        context.user_data['message_count'] = 0
    
    context.user_data['message_count'] += 1

    # Ejecutar la función síncrona de Gemini en el ThreadPoolExecutor
    # Esto libera el event loop de Telegram mientras Gemini responde.
    try:
        reply = await asyncio.get_event_loop().run_in_executor(
            executor,
            generate_response_sync,
            update.message.text.strip()
        )
    except Exception as e:
        logger.error(f"Error al ejecutar Gemini en el ThreadPool: {e}")
        reply = "Disculpá, hubo un problema al procesar tu solicitud."

    # Enviar respuesta principal
    try:
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Error al enviar la respuesta principal a Telegram: {e}. Causa probable: Event loop cerrado.")
        return 
        
    # Cada 4 mensajes: dar consejo adicional
    if context.user_data['message_count'] % 4 == 0:
        consejo_prompt = "Dame un consejo breve para manejar el estrés o mejorar el bienestar emocional."
        
        # Generar el consejo en el ThreadPoolExecutor
        consejo = await asyncio.get_event_loop().run_in_executor(
            executor,
            generate_response_sync,
            consejo_prompt
        )
        
        try:
            await update.message.reply_text(f"💡 Un pequeño pensamiento extra: {consejo}")
        except Exception as e:
            logger.error(f"Error al enviar el consejo a Telegram: {e}")


# ==========================
# CONFIGURAR APLICACIÓN TELEGRAM
# ==========================
application = (
    Application.builder()
    .token(TOKEN)
    .get_updates_pool_timeout(5) 
    .build()
)

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


# ==========================
# WEBHOOK PARA RAILWAY (FLASK)
# ==========================
app = Flask(__name__)
webhook_set = False

# Inicialización síncrona garantizada antes del primer request
@app.before_first_request
def setup_webhook_once():
    """Ejecuta la inicialización del bot y establece el webhook de forma síncrona."""
    global webhook_set
    if webhook_set:
        return
        
    try:
        application.initialize()
        webhook_url = f"https://{RAILWAY_URL}/{TOKEN}"
        
        # Usamos asyncio.run() para ejecutar el set_webhook síncrona
        asyncio.run(application.bot.set_webhook(webhook_url))
        
        logger.info(f"✅ Webhook establecido correctamente en: {webhook_url}")
        webhook_set = True
    except Exception as e:
        logger.error(f"❌ Error durante la inicialización del webhook: {e}")


@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook():
    """Maneja las actualizaciones de Telegram recibidas por el webhook."""
    try:
        # 1. Obtener la actualización
        json_update = await request.get_json(force=True)
        update = Update.de_json(json_update, application.bot)
        
        # 2. Procesar la actualización. Esto se ejecutará en el event loop de Flask/Gunicorn
        await application.process_update(update)
        
        # 3. Retornar OK inmediatamente
        return "OK", 200
    except Exception as e:
        logger.error(f"Error procesando el webhook: {e}")
        # Retornar 200 OK a Telegram para evitar reintentos, pero registrar el error.
        return "OK", 200 

@app.route("/")
def home():
    """Ruta para verificar que el servidor está activo."""
    return "PazOhrBot está activo y esperando webhooks. 🕊️", 200
