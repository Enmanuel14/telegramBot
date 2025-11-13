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

# **Modelo correcto para la API V1Beta**
MODEL_NAME = "gemini-2.5-flash" 
client = genai.Client(api_key=GEMINI_API_KEY)
# Pool de hilos para ejecutar llamadas síncronas a Gemini y manejar updates aisladamente
executor = concurrent.futures.ThreadPoolExecutor(max_workers=10) 

# ==========================
# FUNCIONES (Ahora síncronas, se ejecutan en el ThreadPool)
# ==========================
def generate_response_sync(contents: list):
    """
    Genera una respuesta de Gemini de forma SÍNCRONA. 
    NOTA: El SYSTEM_PROMPT debe estar INYECTADO en el primer turno del usuario.
    """
    
    try:
        # CORRECCIÓN CLAVE: Eliminamos cualquier argumento de system_instruction/generation_config.
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents # Enviamos el historial completo (incluyendo el prompt inyectado)
        )
        return response.text.strip()
    except Exception as e:
        # Detalle de log mejorado
        logger.error(f"Error al llamar a Gemini (Modelo: {MODEL_NAME}): {e}")
        return "Disculpe, he experimentado un inconveniente técnico con la inteligencia artificial. ¿Podría reiterar su mensaje? Agradezco su comprensión."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /start e inicializa el historial de chat."""
    
    # Reiniciar la memoria al iniciar
    context.user_data['chat_history'] = []
    context.user_data['message_count'] = 0
    
    # Usar context.bot.send_message para mayor estabilidad en el ThreadPool
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="¡Bienvenido/a! Soy PazOhrBot, su acompañante virtual para el bienestar emocional. "
             "Puede compartir cómo se siente y le asistiré para encontrar la calma y el equilibrio."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja mensajes de texto, guarda el historial de chat y genera la respuesta.
    """
    
    # 1. Inicializar o recuperar datos
    chat_history = context.user_data.get('chat_history', [])
    message_count = context.user_data.get('message_count', 0)
    
    message_count += 1
    user_text = update.message.text.strip()
    
    # 2. **CORRECCIÓN CLAVE GEMINI**: Inyectar SYSTEM_PROMPT en el primer mensaje
    if not chat_history:
        # Si no hay historial, prepend el SYSTEM_PROMPT al mensaje del usuario
        user_content = f"{SYSTEM_PROMPT}\n\nUSER QUERY: {user_text}"
        current_contents = [{"role": "user", "parts": [{"text": user_content}]}]
    else:
        # Si ya hay historial, simplemente agregamos el nuevo mensaje del usuario
        current_contents = chat_history + [{"role": "user", "parts": [{"text": user_text}]}]

    # 3. Ejecutar la función síncrona de Gemini DIRECTAMENTE (Ya estamos en un hilo)
    reply = "Disculpe, ha ocurrido un error al procesar su solicitud."
    
    try:
        # CORRECCIÓN CLAVE STABILITY: Llamada síncrona directa. No necesitamos run_in_executor 
        # aquí porque la función generate_response_sync es síncrona y ya se ejecuta en un thread.
        reply = generate_response_sync(current_contents)
        
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
        logger.error(f"Error al enviar la respuesta principal a Telegram (Event loop closed): {e}.")
        return 
        
    # 6. Cada 4 mensajes: dar consejo adicional
    if message_count % 4 == 0:
        consejo_prompt = "Proporcione un consejo breve y profesional para manejar el estrés o mejorar el bienestar emocional."
        
        # Generar el consejo de forma síncrona
        consejo = generate_response_sync([{"role": "user", "parts": [{"text": consejo_prompt}]}] )
        
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

# **INICIALIZACIÓN CRÍTICA: Se ejecuta al cargar el script, fuera de los hooks de Flask**
async def setup_webhook():
    """Inicializa la aplicación y configura el webhook de forma asíncrona."""
    global webhook_set
    try:
        await application.initialize()
        webhook_url = f"https://{RAILWAY_URL}/{TOKEN}"
        await application.bot.set_webhook(webhook_url)
        
        webhook_set = True
        logger.info(f"✅ Webhook establecido correctamente en: {webhook_url}")
    except Exception as e:
        logger.error(f"❌ Error durante la inicialización asíncrona: {e}. Asegure que RAILWAY_URL es correcta.")
        webhook_set = False

# Ejecutar el setup al iniciar el script
try:
    asyncio.run(setup_webhook())
except Exception as e:
    logger.error(f"❌ Error al ejecutar asyncio.run(setup_webhook): {e}")


@app.route(f"/{TOKEN}", methods=["POST"])
def webhook(): 
    """Maneja las actualizaciones de Telegram recibidas por el webhook."""
    
    logger.info("🟢 Webhook recibido de Telegram. Procesando actualización en segundo plano.")
    
    if not webhook_set:
        logger.error("❌ Recibido webhook, pero la inicialización falló o no se completó.")
        return "Error", 500
        
    try:
        json_update = request.get_json(force=True)
        update = Update.de_json(json_update, application.bot)
        
        # 2. **PASO CRÍTICO**: Delegar el procesamiento a un hilo aislado 
        def run_update_process():
            # 1. Crear un nuevo loop específico para este hilo
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # 2. Ejecutar el proceso asíncrono
                loop.run_until_complete(application.process_update(update))
                
                # 3. Limpieza: Cierra el loop después de usarlo. 
                loop.close() 
                
                logger.info("✅ Procesamiento de actualización completado en el ThreadPool.")
            except Exception as e:
                logger.error(f"❌ Error crítico en el ThreadPool al procesar la actualización: {e}")

        # Enviar la tarea al ThreadPoolExecutor para su ejecución en segundo plano
        executor.submit(run_update_process)
        
        # 3. Retornar OK inmediatamente.
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Error procesando el webhook ANTES de enviar al ThreadPool: {e}")
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
