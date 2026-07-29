import logging
import re
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from bs4 import BeautifulSoup
from curl_cffi import requests as async_curl_requests
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tasas_bot")

# Estructura en memoria para almacenar los resultados del scraping
latest_rates = {
    "usd": None,
    "eur": None,
    "date": None,
    "time": None,
    "updated_at": None,
    "status": "pending"
}

# --- FUNCIÓN DE SCRAPING CON CURL_CFFI (Bypass Cloudflare 403) ---
async def fetch_rates():
    global latest_rates
    url = "https://eltoque.com/"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }

    logger.info("Iniciando scraping de El Toque...")
    try:
        # Usa impersonate="chrome120" para replicar la huella TLS de un navegador real
        response = await async_curl_requests.AsyncSession().get(
            url, 
            headers=headers, 
            impersonate="chrome120", 
            timeout=15.0
        )
        
        logger.info(f"Código de respuesta HTTP: {response.status_code}")
        
        if response.status_code != 200:
            raise Exception(f"HTTP Error {response.status_code}")

        soup = BeautifulSoup(response.text, "html.parser")
        usd_val, eur_val = None, None

        # Estrategia 1: Spans de clase específica
        price_spans = soup.find_all("span", class_=lambda c: c and "font-extrabold" in c and "text-lg" in c)
        for span in price_spans:
            parent = span.find_parent(lambda tag: tag.name in ['div', 'tr', 'li', 'article'] and ("USD" in tag.get_text().upper() or "EUR" in tag.get_text().upper()))
            raw_text = span.get_text().replace('\xa0', ' ').strip()
            match_number = re.search(r"[\d\.,]+", raw_text)
            
            if match_number:
                clean_value = match_number.group(0)
                if parent:
                    parent_text = parent.get_text().upper()
                    if "USD" in parent_text and not usd_val:
                        usd_val = clean_value
                    elif "EUR" in parent_text and not eur_val:
                        eur_val = clean_value

        # Estrategia 2: Regex de respaldo sobre el texto completo
        if not usd_val or not eur_val:
            full_text = soup.get_text()
            if not usd_val:
                usd_match = re.search(r"USD[^\d]*([\d\.,]+)\s*CUP", full_text, re.IGNORECASE)
                if usd_match:
                    usd_val = usd_match.group(1)
            if not eur_val:
                eur_match = re.search(r"EUR[^\d]*([\d\.,]+)\s*CUP", full_text, re.IGNORECASE)
                if eur_match:
                    eur_val = eur_match.group(1)

        now_utc = datetime.now(timezone.utc)
        latest_rates["usd"] = usd_val
        latest_rates["eur"] = eur_val
        latest_rates["date"] = now_utc.strftime("%Y-%m-%d")
        latest_rates["time"] = now_utc.strftime("%H:%M:%S")
        latest_rates["updated_at"] = now_utc.isoformat()
        latest_rates["status"] = "success"
        
        logger.info(f"✅ Scraping exitoso: USD={usd_val}, EUR={eur_val}")

    except Exception as e:
        logger.error(f"❌ Error durante el scraping: {e}")
        latest_rates["status"] = f"error: {str(e)}"

# --- CICLO DE VIDA DE FASTAPI ---
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ejecuta el scraping inmediatamente al arrancar
    await fetch_rates()
    # Programa el scraping para ejecutarse automáticamente cada hora
    scheduler.add_job(fetch_rates, "interval", hours=1)
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(title="Prueba de Scraping sin Autenticación", lifespan=lifespan)

# --- ENDPOINTS PÚBLICOS DE PRUEBA ---

@app.get("/")
def read_root():
    return {
        "message": "Servidor de prueba activo",
        "endpoints": {
            "ver_tasas": "/api/v1/rates",
            "forzar_actualizacion": "/api/v1/rates/refresh"
        }
    }

@app.get("/api/v1/rates")
def get_rates():
    """Retorna las últimas tasas almacenadas en memoria."""
    return latest_rates

@app.post("/api/v1/rates/refresh")
async def force_refresh():
    """Fuerza una nueva ejecución del scraper inmediatamente."""
    await fetch_rates()
    return {"message": "Petición de actualización ejecutada", "data": latest_rates}