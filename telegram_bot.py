import os
import asyncio
import concurrent.futures # Ya no se usa, pero lo dejamos por si acaso
import json
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
Tu objetivo es proporcionar apoyo y consejos de bienestar emocional.
Responde de forma profesional, formal, respetuosa y con un lenguaje internacional neutral en español.
Mantente enfocado en el bienestar y las necesidades del usuario.
Responde de forma concisa, no más de 4 oraciones."""

MODEL_NAME = "gemini-2.5-flash" 
client = genai.Client(api_key=GEMINI_API_KEY)
# Eliminamos el executor, la ejecución será síncrona en el hilo principal
# executor = concurrent.futures.ThreadPoolExecutor(max_workers=10) 

# ==========================
# FUNCIONES AUXILIARES
# ==========================
def generate_response_sync(contents: list):
    """Genera una respuesta de Gemini de forma SÍNCRONA."""
    try:
        # Llama a la API de forma síncrona, bloqueando temporalmente el hilo.
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents 
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error al llamar a Gemini: {e}")
        # Este mensaje se enviará si falla la API de Gemini
        return "Lo siento, hubo un error al procesar tu mensaje. Intenta nuevamente más tarde."

# Eliminamos safe_run_in_executor ya que no usaremos un pool de hilos
# async def safe_run_in_executor(func, *args):
#     ...

# ==========================
# HANDLERS (ASÍNCRONOS)
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /start e inicializa el historial de chat."""
    if 'chat_history' not in context.user_data:
        context.user_data['chat_history'] = []
        context.user_data['message_count'] = 0
    
    # Llamada síncrona para evitar "Event loop is closed"
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="¡Bienvenido/a! Soy PazOhrBot, su acompañante virtual para el bienestar emocional. "
             "Puede compartir cómo se siente y le asistiré para encontrar la calma y el equilibrio."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja mensajes de texto, guarda el historial de chat y genera la respuesta."""
    
    chat_history = context.user_data.get('chat_history', [])
    message_count = context.user_data.get('message_count', 0)
    message_count += 1
    user_text = update.message.text.strip()
    
    # 2. **MEMORIA GEMINI**: Inyectar SYSTEM_PROMPT (Lógica solicitada)
    if not chat_history:
        user_content = f"{SYSTEM_PROMPT}\n\nUSER QUERY: {user_text}"
        current_contents = [{"role": "user", "parts": [{"text": user_content}]}]
    else:
        current_contents = chat_history + [{"role": "user", "parts": [{"text": user_text}]}]

    reply = "Disculpe, ha ocurrido un error al procesar su solicitud."
    
    try:
        # 3. Ejecutamos la llamada a Gemini DIRECTAMENTE (SÍNCRONA)
        # Esto bloquea el hilo, pero es más estable que el executor que estaba fallando
        reply = generate_response_sync(current_contents)
        
        # 4. Actualizar el historial (Solo si la respuesta no es el mensaje de error de fallback)
        if not reply.startswith("Lo siento, hubo un error"):
            new_history = current_contents + [{"role": "model", "parts": [{"text": reply}]}]
            context.user_data['chat_history'] = new_history
        else:
            logger.error("Gemini devolvió el mensaje de error de fallback.")
        
    except Exception as e:
        logger.error(f"Error al ejecutar Gemini de forma síncrona: {e}")
        
    # 5. Enviar respuesta principal
    try:
        # Llamada síncrona para evitar "Event loop is closed"
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=reply
        )
    except Exception as e:
        logger.error(f"Error al enviar la respuesta principal a Telegram: {e}.")
        return 
        
    # 6. Cada 4 mensajes: dar consejo adicional
    if message_count % 4 == 0:
        consejo_prompt = "Proporcione un consejo breve y profesional para manejar el estrés o mejorar el bienestar emocional."
        
        # Ejecutamos el consejo también de forma SÍNCRONA
        consejo = generate_response_sync(
            [{"role": "user", "parts": [{"text": consejo_prompt}]}]
        )
        
        try:
            # Llamada síncrona para evitar "Event loop is closed"
            context.bot.send_message(
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
# WEBHOOK CONFIGURATION (Simplified and Stable for Production)
# ==========================
app = Flask(__name__)

# Inicializar y configurar el Webhook
async def setup_webhook():
    """Inicializa la aplicación y configura el webhook de Telegram."""
    try:
        await application.initialize()
        
        webhook_url = f"https://{RAILWAY_URL}/{TOKEN}"
        
        # Seteamos el Webhook en Telegram
        await application.bot.set_webhook(webhook_url)
        
        # Arrancamos la Application de PTB
        await application.start()
        
        logger.info(f"✅ Application iniciada y Webhook establecido en: {webhook_url}")
    except Exception as e:
        logger.error(f"❌ Error durante la inicialización asíncrona: {e}.")
        raise

# Ejecutar setup al iniciar el script
try:
    # Usamos asyncio.run() para ejecutar setup_webhook y esperar a que termine
    asyncio.run(setup_webhook())
except Exception as e:
    logger.error(f"❌ Error fatal al ejecutar asyncio.run(setup_webhook): {e}")


@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook(): 
    """
    Maneja las actualizaciones de Telegram recibidas por el webhook.
    Convierte la solicitud JSON en un objeto Update y lo procesa.
    """
    logger.info("🟢 Webhook recibido de Telegram. Enviando a cola de procesamiento de PTB.")
    
    try:
        # request.get_json() devuelve un dict síncrono.
        update_json = request.get_json()
        
        if update_json is None:
            logger.error("❌ Webhook recibido con cuerpo vacío o no JSON.")
            return "OK", 200 
            
        update = Update.de_json(update_json, application.bot)
        
        # application.process_update es async y maneja la ejecución de handlers.
        await application.process_update(update)
        
        # Telegram necesita una respuesta 200 OK inmediatamente.
        return "OK", 200
        
    except Exception as e:
        logger.error(f"❌ Error procesando el webhook: {e}")
        # Siempre devolvemos 200 OK para evitar reintentos infinitos de Telegram.
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
