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
    # Limpiamos el URL por si tiene el prefijo https://
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

# **CAMBIO AQUÍ: Usar gemini-2.5-flash para compatibilidad con la API V1Beta**
MODEL_NAME = "gemini-2.5-flash" 
client = genai.Client(api_key=GEMINI_API_KEY)
# Pool de hilos para ejecutar llamadas síncronas a Gemini y manejar updates aisladamente
executor = concurrent.futures.ThreadPoolExecutor(max_workers=10) 

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
        # Detalle de log mejorado
        logger.error(f"Error al llamar a Gemini (Modelo: {MODEL_NAME}): {e}")
        return "Disculpá, tuve un pequeño fallo técnico con la IA. ¿Podrías repetirme lo que me decías? 🙏"


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
    try:
        # Usamos run_in_executor con el loop del hilo actual para correr la función síncrona
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

# **INICIALIZACIÓN CRÍTICA: Se ejecuta al cargar el script, fuera de los hooks de Flask**
async def setup_webhook():
    """Inicializa la aplicación y configura el webhook de forma asíncrona."""
    global webhook_set
    try:
        # **CAMBIO CLAVE: Llamar a initialize() con await**
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
    # Esto captura errores si asyncio.run() falla al inicio
    logger.error(f"❌ Error al ejecutar asyncio.run(setup_webhook): {e}")


@app.route(f"/{TOKEN}", methods=["POST"])
def webhook(): 
    """Maneja las actualizaciones de Telegram recibidas por el webhook."""
    
    logger.info("🟢 Webhook recibido de Telegram. Procesando actualización en segundo plano.")
    
    if not webhook_set:
        logger.error("❌ Recibido webhook, pero la inicialización falló o no se completó.")
        return "Error", 500
        
    try:
        # 1. Obtener la actualización de forma síncrona
        json_update = request.get_json(force=True)
        update = Update.de_json(json_update, application.bot)
        
        # 2. **PASO CRÍTICO**: Delegar el procesamiento a un hilo aislado
        def run_update_process():
            try:
                # Crea un nuevo loop y lo ejecuta hasta que el procesamiento de Telegram termine
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # process_update ya no fallará porque application.initialize() fue esperada
                loop.run_until_complete(application.process_update(update))
                logger.info("✅ Procesamiento de actualización completado en el ThreadPool.")
            except Exception as e:
                # Si falla aquí, es probablemente un error de red al enviar la respuesta
                logger.error(f"❌ Error crítico en el ThreadPool al procesar la actualización: {e}")


        # Enviar la tarea al ThreadPoolExecutor para su ejecución en segundo plano
        executor.submit(run_update_process)
        
        # 3. Retornar OK inmediatamente.
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Error procesando el webhook ANTES de enviar al ThreadPool: {e}")
        # Retorna 200 OK para evitar reintentos de Telegram, pero con log de error.
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
