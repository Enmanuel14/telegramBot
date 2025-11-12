import os
import json
import logging
import asyncio
import random
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import httpx # Se recomienda usar 'httpx' o 'aiohttp' para llamadas asíncronas en producción

# --- CONFIGURACIÓN DE LOGGING ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- CONSTANTES ---
CONSEJO_INTERVALO = 5  # Dar consejo cada 5 mensajes.
HISTORY_FILE = 'telegram_chat_history.json' # Archivo para almacenar historial/contador
FIRST_MESSAGE_KEY = "is_first_message" # Clave para detectar el primer mensaje

# --- VARIABLES DE ENTORNO ---
# El token debe configurarse en Railway como TELEGRAM_BOT_TOKEN
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TU_TOKEN_DE_BOT_AQUI")
# La clave debe configurarse en Railway como GEMINI_API_KEY
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "") 
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"

# --- PROMPTS Y CONSEJOS ESPECÍFICOS ---
SYSTEM_PROMPT = """Eres un psicólogo virtual llamado PazOhrBot. Tu objetivo es proporcionar apoyo, consejos de bienestar emocional y mantener una conversación empática y confidencial. Responde de forma cálida, reflexiva y en español. Mantente enfocado en el bienestar del usuario. Responde de forma concisa, no más de 4 oraciones - Eres de Nicaragua."""

# Prompt para que Gemini evalúe la emoción (solo debe responder ANSIEDAD o OTRO)
ANSIEDAD_PROMPT = "Analiza el siguiente texto: '{user_text}'. Si el tono principal es de ansiedad, estrés, o preocupación intensa, responde SOLAMENTE con la palabra 'ANSIEDAD'. En cualquier otro caso (tristeza, felicidad, aburrimiento, etc.), responde SOLAMENTE con la palabra 'OTRO'."

CONSEJOS_ANSIEDAD = [
    f"""**Técnica de la Respiración 4-4-6:** Siente tu cuerpo tenso. Ahora, inhala lentamente contando hasta 4, mantén el aire 4 segundos, y exhala contando hasta 6. Esto calma el sistema nervioso. [Image of diaphragmatic breathing technique]""",
    """**Anclaje a la Realidad (5-4-3-2-1):** Cuando la ansiedad suba, nombra: 5 cosas que puedes ver, 4 cosas que puedes tocar, 3 cosas que puedes oír, 2 cosas que puedes oler, y 1 cosa que puedes saborear. Te trae al presente.""",
    """**Tensión/Relajación Progresiva:** Tensa un grupo muscular (puños, hombros) por 5 segundos y suéltalo completamente, sintiendo la diferencia. Repite 3 veces. Es una forma física de liberar la ansiedad.""",
]

CONSEJOS_GENERALES = [
    """**Registro de Gratitud:** Antes de dormir, nombra tres cosas, por pequeñas que sean, que te hicieron sentir bien hoy. Enfocarse en lo positivo cambia tu perspectiva.""",
    """**Mindfulness de 5 Minutos:** Dedica 5 minutos a concentrarte solo en una actividad sensorial, como beber un vaso de agua o escuchar música, sin pensar en otra cosa. Entrena tu mente para enfocarse.""",
    """**Pequeños Logros:** ¿Qué pequeña tarea puedes completar ahora? Completar algo, por simple que sea, genera una sensación de control y logro.""",
]

# --- FUNCIONES DE ALMACENAMIENTO DE ESTADO (PARA RAILWAY) ---

def load_history():
    """Carga el historial completo de chats desde el archivo."""
    try:
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Si el archivo no existe o está vacío/corrupto, devuelve un diccionario vacío.
        return {}

def save_history(history):
    """Guarda el historial completo de chats en el archivo."""
    # Nota: En entornos como Railway, la escritura puede ser limitada.
    # Si la app escala mucho, se recomienda usar una base de datos (Firestore).
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f)
    except Exception as e:
        logger.error(f"Error al guardar el historial: {e}")


# --- FUNCIÓN DE LLAMADA A LA API DE GEMINI (USANDO httpx para ser asíncrono) ---

async def call_gemini_api(payload: dict) -> str:
    """Función real para llamar a la API de Gemini usando httpx (asíncrono)."""
    
    if not GEMINI_API_KEY:
        return "Disculpa, Psicobot no tiene acceso a la IA. Asegúrate de configurar la clave de API."
    
    headers = {
        'Content-Type': 'application/json',
    }
    
    # Lógica de reintento con httpx (similar a la que teníamos en el bot de Twilio)
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}", headers=headers, json=payload)
                response.raise_for_status() # Lanza una excepción para códigos de error 4xx/5xx

                result = response.json()
                
                # Extraer el texto generado
                text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')

                if not text or len(text.strip()) < 5:
                    raise Exception("Respuesta de Gemini vacía o filtrada.")
                
                logger.info(f"Respuesta de Gemini exitosa en intento {attempt + 1}.")
                return text

        except Exception as e:
            logger.error(f"Fallo de Gemini en el intento {attempt + 1}: {e}")
            if attempt < 2:
                await asyncio.sleep(2) # Espera 2 segundos antes de reintentar
            else:
                # Fallo total
                return f"🤖 Lo siento, la conexión con la IA falló después de varios intentos. Intenta de nuevo en un minuto. (Error: {e})"
    
    return "Error desconocido en la comunicación con la IA."


async def generate_gemini_response(prompt: str, user_id: str, is_first_message: bool) -> str:
    """
    Gestiona el historial de chat, llama a la API de Gemini,
    y añade consejos proactivos.
    """
    
    # 1. LOGICA DEL PRIMER MENSAJE
    if is_first_message:
        welcome_message = (
            "🌟 ¡Hola! Soy **PazOhrBot**, tu compañero de bienestar emocional.\n\n"
            "Aquí encontrarás un espacio seguro y confidencial, disponible 24/7 para escucharte sin juicios.\n\n"
            "Simplemente, **escríbeme lo que sientes, lo que piensas o lo que te preocupa**. Estoy programado para ofrecer apoyo cálido, validación y, si lo necesitas, técnicas prácticas para manejar el día a día.\n\n"
            "¿Qué tienes en mente hoy? Empecemos cuando quieras. 😊"
        )
        # 6. Guardar estado del primer mensaje para que no se repita
        history = load_history()
        user_data = history.get(user_id, {"messages": [], "counter": 0})
        user_data["messages"].append({'role': 'model', 'parts': [{'text': "Mensaje de bienvenida"}]}) # Añade un marcador
        history[user_id] = user_data
        save_history(history)

        return welcome_message

    # 2. CARGAR Y PREPARAR HISTORIAL (Para la IA y el contador)
    history = load_history()
    user_data = history.get(user_id, {"messages": [], "counter": 0})
    current_messages = user_data["messages"]
    message_counter = user_data["counter"]

    # Limitar el historial de mensajes de contexto (solo roles 'user' y 'model')
    context_messages_raw = [
        {'role': 'user', 'parts': [{'text': msg['parts'][0]['text']}]} if msg['role'] == 'user' else 
        {'role': 'model', 'parts': [{'text': msg['parts'][0]['text']}]}
        for msg in current_messages[-8:] # Últimos 8, que son 4 turnos
        if msg['role'] in ['user', 'model']
    ]

    # 3. CONSTRUIR PAYLOAD DE GEMINI (CON CONTEXTO)
    
    # 3a. Payload para la respuesta principal
    content_list = context_messages_raw + [
        {'role': 'user', 'parts': [{'text': prompt}]} # El mensaje actual del usuario
    ]

    payload_main = {
        "contents": content_list, 
        "config": {
            "systemInstruction": SYSTEM_PROMPT
        }
    }

    # 4. LLAMADA PRINCIPAL A GEMINI
    reply_text = await call_gemini_api(payload_main)

    # 5. LÓGICA DE CONSEJO PROACTIVO (CADA 5 MENSAJES)
    message_counter += 1
    
    final_response_text = reply_text
    
    if message_counter % CONSEJO_INTERVALO == 0:
        
        # A) Detección de Ansiedad
        anxiety_prompt_filled = ANSIEDAD_PROMPT.format(user_text=prompt)
        
        anxiety_payload = {
            "contents": [{'role': 'user', 'parts': [{'text': anxiety_prompt_filled}]}],
            "config": {
                "systemInstruction": "Eres un clasificador de emociones. Responde SOLAMENTE 'ANSIEDAD' o 'OTRO'."
            }
        }
        
        # Llamada para detectar emoción
        emotion_detection = await call_gemini_api(anxiety_payload)
        
        consejo = ""
        if "ANSIEDAD" in emotion_detection.upper():
            # Si detecta ansiedad, da un consejo específico y lo añade al final de la respuesta.
            consejo = random.choice(CONSEJOS_ANSIEDAD)
            final_response_text += f"\n\n🧘‍♀️ **Un momento para ti:** Detecto algo de tensión, ¿quieres probar esto? \n{consejo}"
        else:
            # Si no es ansiedad, da un consejo general de bienestar.
            consejo = random.choice(CONSEJOS_GENERALES)
            final_response_text += f"\n\n✨ **Un pequeño recordatorio:** Un pequeño paso para tu bienestar. \n{consejo}"
        
        # Reiniciar contador después de dar un consejo
        message_counter = 0

    # 6. ACTUALIZAR HISTORIAL
    current_messages.append({'role': 'user', 'parts': [{'text': prompt}]})
    current_messages.append({'role': 'model', 'parts': [{'text': final_response_text}]})
    
    # Limpiar historial si es muy largo para evitar sobrecargar la memoria
    user_data["messages"] = current_messages[-20:] # Mantiene los últimos 20 mensajes (10 turnos)
    user_data["counter"] = message_counter
    history[user_id] = user_data
    save_history(history)

    return final_response_text

# --- MANEJADOR DE MENSAJES DE TELEGRAM ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja el mensaje entrante y llama a la IA para obtener la respuesta."""
    if not update.message or not update.message.text:
        if update.message:
            await update.message.reply_text("Disculpa, Psicobot solo puede responder a mensajes de texto.")
        return

    user_message = update.message.text
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    
    logger.info(f"Mensaje recibido de {user_id} en chat {chat_id}: {user_message}")

    # Cargar historial para verificar si es el primer mensaje
    history = load_history()
    is_first_message = user_id not in history or not history[user_id].get("messages")
    
    # Muestra el indicador de "escribiendo..."
    await update.message.chat.send_action(action="typing")
    
    # Obtener respuesta de Gemini y el consejo proactivo
    gemini_text = await generate_gemini_response(user_message, user_id, is_first_message)
    
    logger.info(f"Respuesta de Gemini: {gemini_text[:50]}...")

    # Enviar la respuesta a Telegram
    await update.message.reply_text(gemini_text, parse_mode='Markdown')


# --- FUNCIÓN PRINCIPAL DE INICIO ---
def main() -> None:
    """Inicia el bot de Telegram."""
    # Intentar crear el archivo de historial si no existe
    if not os.path.exists(HISTORY_FILE):
        save_history({})
        logger.info(f"Archivo de historial '{HISTORY_FILE}' inicializado.")
        
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "TU_TOKEN_DE_BOT_AQUI":
        logger.error("¡ERROR! El token de TELEGRAM_BOT_TOKEN no está configurado.")
        print("***********************************************************************************************************")
        print("* ¡ERROR! Por favor, configura la variable de entorno TELEGRAM_BOT_TOKEN con el token de BotFather. *")
        print("***********************************************************************************************************")
        return

    # Se usa el Application.builder().token() para crear el bot.
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Añadir un manejador de mensajes de texto, excluyendo comandos (como /start)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Bot de Telegram iniciado. Escuchando mensajes...")
    
    # Inicia el polling (el método más común y fácil para bots simples en Railway)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()