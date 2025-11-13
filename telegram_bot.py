import os
import asyncio
import concurrent.futures 
import json
import logging
import traceback 
# Eliminamos 'flask' y 'request' ya que PTB manejará el servidor web
# from flask import Flask, request 

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from google import genai
from dotenv import load_dotenv

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

# Mantenemos el ThreadPoolExecutor para ejecutar la llamada SÍNCRONA de Gemini 
# en el hilo de fondo de forma segura.
executor = concurrent.futures.ThreadPoolExecutor(max_workers=5) 

# ==========================
# FUNCIONES AUXILIARES
# ==========================
def generate_response_sync(contents: list):
    """Genera una respuesta de Gemini de forma SÍNCRONA."""
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents 
        )
        return response.text.strip()
    except Exception as e:
        error_details = traceback.format_exc()
        status_code = getattr(e, 'http_status', 'N/A')
        
        logger.error(f"❌ Error al llamar a Gemini. HTTP Status: {status_code}. Detalles: {e}")
        logger.error(f"Stack Trace Completo: {error_details}")

        return "Lo siento, hubo un error al procesar tu mensaje. Intenta nuevamente más tarde."

async def run_in_executor(func, *args):
    """Ejecuta una función síncrona en el executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, func, *args)


# ==========================
# HANDLERS (ASÍNCRONOS)
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /start."""
    if 'chat_history' not in context.user_data:
        context.user_data['chat_history'] = []
        context.user_data['message_count'] = 0
    
    await context.bot.send_message(
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

    # Se inicializa con el mensaje de error general
    reply = "Disculpe, ha ocurrido un error al procesar su solicitud."
    
    # Ejecutamos la llamada a Gemini ASÍNCRONAMENTE en el executor
    try:
        reply = await run_in_executor(generate_response_sync, current_contents)
        
        # 4. Actualizar el historial (Solo si la respuesta no es el mensaje de error de fallback)
        if not reply.startswith("Lo siento, hubo un error"):
            new_history = current_contents + [{"role": "model", "parts": [{"text": reply}]}]
            context.user_data['chat_history'] = new_history
        else:
            logger.warning("Gemini devolvió el mensaje de error de fallback. No se actualizará el historial.")
            
    except Exception as e:
        logger.error(f"Error fatal al ejecutar Gemini: {e}")
        
    # 5. Enviar respuesta principal
    try:
        # Aquí el 'await' ya no falla porque el event loop lo controla PTB
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
        
        # Ejecutamos el consejo también ASÍNCRONAMENTE en el executor
        consejo = await run_in_executor(
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
# BLOQUE DE EJECUCIÓN (Usando el servidor web de PTB)
# ==========================
if __name__ == "__main__":
    # La ruta de URL que Telegram llamará (la misma que la RAILWAY_URL)
    webhook_path = f"/{TOKEN}"
    webhook_url = f"https://{RAILWAY_URL}{webhook_path}"
    
    logger.info(f"✨ Iniciando bot con run_webhook. Webhook URL: {webhook_url}")
    
    # Usamos Application.run_webhook para dejar que PTB gestione el event loop, 
    # eliminando el conflicto de 'Event loop is closed'.
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=webhook_path,
        webhook_url=webhook_url,
    )
