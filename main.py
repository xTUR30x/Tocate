import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tasas_bot")

latest_rates = {
    "usd": None,
    "eur": None,
    "date": None,      # Ejemplo: "2026-07-22"
    "time": None,      # Ejemplo: "23:28:07"
    "updated_at": None, # ISO 8601 Completo
    "status": "pending"
}

async def fetch_rates():
    global latest_rates
    url = "https://eltoque.com/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    logger.info("Iniciando scraping de El Toque...")
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        
        usd_val = None
        eur_val = None

        # Estrategia 1: Buscar spans con clases específicas de precio
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

        # Estrategia 2 (Respaldo): Regex en el texto completo
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

        # Capturar fecha y hora actuales en UTC
        now_utc = datetime.now(timezone.utc)

        latest_rates["usd"] = usd_val
        latest_rates["eur"] = eur_val
        latest_rates["date"] = now_utc.strftime("%Y-%m-%d")
        latest_rates["time"] = now_utc.strftime("%H:%M:%S")
        latest_rates["updated_at"] = now_utc.isoformat()
        latest_rates["status"] = "success"
        
        logger.info(f"Tasas extraídas: USD={usd_val}, EUR={eur_val} (Fecha: {latest_rates['date']}, Hora: {latest_rates['time']})")

    except Exception as e:
        logger.error(f"Error realizando el scraping: {e}")
        latest_rates["status"] = f"error: {str(e)}"

# Scheduler para ejecutar la tarea cada 1 hora
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await fetch_rates()
    scheduler.add_job(fetch_rates, "interval", hours=1)
    scheduler.start()
    logger.info("Scheduler iniciado: Tarea programada cada 1 hora.")
    yield
    scheduler.shutdown()
    logger.info("Scheduler detenido.")

app = FastAPI(
    title="API de Tasas de Cambio (El Toque)",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/")
def read_root():
    return {
        "message": "API de Tasas de Cambio activa",
        "docs": "/docs",
        "endpoint": "/api/v1/rates"
    }

@app.get("/api/v1/rates")
def get_rates():
    return latest_rates

@app.post("/api/v1/rates/refresh")
async def force_refresh():
    await fetch_rates()
    return {"message": "Actualización forzada completada", "data": latest_rates}