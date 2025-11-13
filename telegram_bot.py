import os
import asyncio
import concurrent.futures
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from google import genai
from dotenv import load_dotenv
import logging
import threading 

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
    # Limpiamos el URL por si tiene el prefijo https://
    RAILWAY_URL = RAILWAY_URL.replace("https://", "")
    logger.info(f"✅ Variables cargadas. Webhook URL base: https://{RAILWAY_URL}/{TOKEN}")

# ==========================
# CONFIGURACIÓN DEL SISTEMA Y CLIENTE GEMINI
# ==========================
SYSTEM_PROMPT = """Eres un psicólogo virtual llamado PazOhrBot.
Tu objetivo es proporcionar apoyo y consejos de bienestar emocional.
Responde de forma profesional, formal, respetuosa y con un lenguaje internacional neutral en español.
Mantente enfocado en el bienestar y las necesidades del usuario.
Responde de forma concisa, no más de 4 oraciones."""

MODEL_NAME = "gemini-2.5-flash" 
client = genai.Client(api_key=GEMINI_API_KEY)
# Pool de hilos para ejecutar llamadas síncronas a Gemini (usado por PTB)
executor = concurrent.futures.ThreadPoolExecutor(max_workers=10) 

# ==========================
# FUNCIONES (Síncronas para Gemini)
# ==========================
def generate_response_sync(contents: list):
    """
    Genera una respuesta de Gemini de forma SÍNCRONA. 
    """
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents 
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error al llamar a Gemini (Modelo: {MODEL_NAME}): {e}")
        return "Disculpe, he experimentado un inconveniente técnico con la inteligencia artificial. ¿Podría reiterar su mensaje? Agradezco su comprensión."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /start e inicializa el historial de chat."""
    
    context.user_data['chat_history'] = []
    context.user_data['message_count'] = 0
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="¡Bienvenido/a! Soy PazOhrBot, su acompañante virtual para el bienestar emocional. "
             "Puede compartir cómo se siente y le asistiré para encontrar la calma y el equilibrio."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja mensajes de texto, guarda el historial de chat y genera la respuesta.
    """
    
    chat_history = context.user_data.get('chat_history', [])
    message_count = context.user_data.get('message_count', 0)
    
    message_count += 1
    user_text = update.message.text.strip()
    
    # 2. **MEMORIA GEMINI**: Inyectar SYSTEM_PROMPT en el primer mensaje
    if not chat_history:
        user_content = f"{SYSTEM_PROMPT}\n\nUSER QUERY: {user_text}"
        current_contents = [{"role": "user", "parts": [{"text": user_content}]}]
    else:
        current_contents = chat_history + [{"role": "user", "parts": [{"text": user_text}]}]

    # 3. Ejecutar la función síncrona de Gemini
    reply = "Disculpe, ha ocurrido un error al procesar su solicitud."
    
    try:
        # Usamos application.bot.loop.run_in_executor para ejecutar la llamada síncrona 
        # de Gemini de forma segura dentro del loop asíncrono de PTB.
        reply = await application.bot.loop.run_in_executor(
            executor, 
            generate_response_sync, 
            current_contents
        )
        
        # 4. Actualizar el historial después de la respuesta exitosa
        new_history = current_contents + [{"role": "model", "parts": [{"text": reply}]}]
        context.user_data['chat_history'] = new_history
        
    except Exception as e:
        logger.error(f"Error al ejecutar Gemini de forma síncrona: {e}")
        
    # 5. Enviar respuesta principal
    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=reply
        )
    except Exception as e:
        logger.error(f"Error al enviar la respuesta principal a Telegram: {e}.")
        return 
        
    # 6. Cada 4 mensajes: dar consejo adicional
    if message_count % 4 == 0:
        consejo_prompt = "Proporcione un consejo breve y profesional para manejar el estrés o mejorar el bienestar emocional."
        
        # Generar el consejo de forma síncrona
        consejo = await application.bot.loop.run_in_executor(
            executor, 
            generate_response_sync, 
            [{"role": "user", "parts": [{"text": consejo_prompt}]}]
        )
        
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"💡 Un pensamiento para su bienestar: {consejo}"
            )
        except Exception as e:
            logger.error(f"Error al enviar el consejo extra a Telegram: {e}")

    # Guardar el contador actualizado
    context.user_data['message_count'] = message_count


# ==========================
# CONFIGURAR APLICACIÓN TELEGRAM
# ==========================
application = (
    Application.builder()
    .token(TOKEN)
    .build()
)

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ==========================
# WEBHOOK PARA RAILWAY (FLASK) - CRITICAL RESTRUCTURE
# ==========================
app = Flask(__name__)
webhook_set = False

# **Función para correr el loop de PTB en un hilo separado**
def run_ptb_worker():
    """Ejecuta el ciclo de vida de la aplicación PTB en un thread daemon."""
    try:
        # run_until_shutdown() ejecuta el loop asíncrono de PTB hasta que se le diga que pare.
        asyncio.run(application.run_until_shutdown())
    except Exception as e:
        logger.error(f"❌ Error al ejecutar el loop de PTB en el thread: {e}")

# **Inicialización: Seteo de Webhook y arranque del Thread Worker**
async def setup_webhook():
    """Inicializa la aplicación, configura el webhook y arranca el worker de PTB."""
    global webhook_set
    try:
        await application.initialize()
        webhook_url = f"https://{RAILWAY_URL}/{TOKEN}"
        await application.bot.set_webhook(webhook_url)
        
        # 1. Arrancar el thread de worker de PTB
        worker_thread = threading.Thread(target=run_ptb_worker, daemon=True)
        worker_thread.start()
        
        # 2. Iniciar las tareas internas del PTB (handlers, etc.)
        await application.start() 
        
        webhook_set = True
        logger.info(f"✅ Worker de PTB iniciado. Webhook establecido en: {webhook_url}")
    except Exception as e:
        logger.error(f"❌ Error durante la inicialización asíncrona: {e}.")
        webhook_set = False

# Ejecutar setup al iniciar el script
try:
    asyncio.run(setup_webhook())
except Exception as e:
    logger.error(f"❌ Error al ejecutar asyncio.run(setup_webhook): {e}")


@app.route(f"/{TOKEN}", methods=["POST"])
def webhook(): 
    """Maneja las actualizaciones de Telegram recibidas por el webhook."""
    
    logger.info("🟢 Webhook recibido de Telegram. Enviando a cola de procesamiento de PTB.")
    
    if not webhook_set:
        logger.error("❌ Recibido webhook, pero la inicialización falló o no se completó.")
        return "Error", 500
        
    try:
        json_update = request.get_json(force=True)
        update = Update.de_json(json_update, application.bot)
        
        # 2. FIX CRÍTICO: Colocar la actualización en la cola de la aplicación PTB.
        # El thread worker iniciado en setup_webhook se encargará de procesarla de forma asíncrona y segura.
        application.update_queue.put(update)
        
        # 3. Retornar OK inmediatamente (sin esperar la respuesta).
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Error procesando el webhook: {e}")
        return "OK", 200 

@app.route("/")
def home():
    """Ruta para verificar que el servidor está activo."""
    return "PazOhrBot está activo y esperando webhooks. 🕊️", 200

# ==========================
# BLOQUE DE EJECUCIÓN (Asegura que Gunicorn/Flask inicie)
# ==========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
