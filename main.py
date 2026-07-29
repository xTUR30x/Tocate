import os
import logging
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from curl_cffi import requests as async_curl_requests
from fastapi import FastAPI, Depends, HTTPException, Security, Header
from fastapi.security import APIKeyHeader
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tasas_bot")

# Carga de variables de entorno (.env en local o panel de Render en producción)
load_dotenv()

ADMIN_MASTER_KEY = os.getenv("ADMIN_MASTER_KEY")

if not ADMIN_MASTER_KEY:
    logger.error("ERROR CRÍTICO: La variable de entorno 'ADMIN_MASTER_KEY' no está configurada.")
    raise RuntimeError("Falta configurar la variable de entorno 'ADMIN_MASTER_KEY'.")

DB_NAME = "api_keys.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key TEXT PRIMARY KEY,
                client_name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        conn.commit()

init_db()

latest_rates = {
    "usd": None,
    "eur": None,
    "date": None,
    "time": None,
    "updated_at": None,
    "status": "pending"
}

# --- SCRAPING CON CURL_CFFI (Bypass de Cloudflare 403) ---
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
        response = await async_curl_requests.AsyncSession().get(
            url, 
            headers=headers, 
            impersonate="chrome120", 
            timeout=15.0
        )
        
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

        # Estrategia 2: Regex de respaldo
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
        
        logger.info(f"Tasas extraídas: USD={usd_val}, EUR={eur_val}")

    except Exception as e:
        logger.error(f"Error realizando el scraping: {e}")
        latest_rates["status"] = f"error: {str(e)}"

# --- SISTEMA DE AUTENTICACIÓN Y VALIDACIÓN ---
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header)):
    if not api_key:
        raise HTTPException(status_code=401, detail="Falta la cabecera 'X-API-Key'")
    
    # Bypass para tu Clave Maestra
    if api_key == ADMIN_MASTER_KEY:
        return api_key

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active, expires_at FROM api_keys WHERE key = ?", (api_key,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=401, detail="API Key no válida")
        
        is_active, expires_at_str = row
        if not is_active:
            raise HTTPException(status_code=403, detail="Esta API Key ha sido desactivada.")
        
        expires_at = datetime.fromisoformat(expires_at_str)
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=403, detail="Suscripción expirada. Renueva tu pago para continuar.")

    return api_key

def verify_admin_key(admin_key: str = Header(..., alias="X-Admin-Key")):
    if admin_key != ADMIN_MASTER_KEY:
        raise HTTPException(status_code=403, detail="Acceso denegado: Clave maestra incorrecta")
    return True

# --- MODELOS DE DATOS DE ADMINISTRACIÓN ---
class KeyCreateRequest(BaseModel):
    client_name: str
    days_valid: int = 30

class KeyUpdateRequest(BaseModel):
    api_key: str
    is_active: bool | None = None
    additional_days: int | None = None
    client_name: str | None = None

# --- PROGRAMADOR DE TAREAS Y LIFESPAN ---
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await fetch_rates()
    scheduler.add_job(fetch_rates, "interval", hours=1)
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(title="API Tasas de Cambio (El Toque)", version="1.2.0", lifespan=lifespan)

@app.get("/")
def read_root():
    return {"message": "API activa", "docs": "/docs", "endpoint": "/api/v1/rates"}

# --- ENDPOINTS PÚBLICOS PROTEGIDOS POR CLIENT API KEY ---
@app.get("/api/v1/rates", dependencies=[Depends(verify_api_key)])
def get_rates():
    return latest_rates

@app.post("/api/v1/rates/refresh", dependencies=[Depends(verify_api_key)])
async def force_refresh():
    await fetch_rates()
    return {"message": "Actualización completada", "data": latest_rates}

# =====================================================================
# --- ENDPOINTS ADMINISTRATIVOS REST (PARA CLIENTES EXTERNOS / UIs) ---
# =====================================================================

@app.get("/admin/keys", dependencies=[Depends(verify_admin_key)])
def list_all_keys():
    """1. Obtener todas las API Keys registradas."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT key, client_name, is_active, created_at, expires_at FROM api_keys")
        rows = cursor.fetchall()
        
    keys = []
    now = datetime.now(timezone.utc)
    for row in rows:
        expires_at = datetime.fromisoformat(row["expires_at"])
        keys.append({
            "key": row["key"],
            "client_name": row["client_name"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "is_expired": now > expires_at
        })
    return {"total": len(keys), "keys": keys}

@app.post("/admin/keys", dependencies=[Depends(verify_admin_key)])
def create_key(payload: KeyCreateRequest):
    """2. Crear una nueva API Key."""
    new_key = f"sk_live_{secrets.token_hex(16)}"
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=payload.days_valid)

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO api_keys (key, client_name, is_active, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (new_key, payload.client_name, 1, now.isoformat(), expires_at.isoformat())
        )
        conn.commit()

    return {
        "message": "Key creada con éxito",
        "client_name": payload.client_name,
        "api_key": new_key,
        "expires_at": expires_at.isoformat(),
        "is_active": True
    }

@app.patch("/admin/keys", dependencies=[Depends(verify_admin_key)])
def update_key(payload: KeyUpdateRequest):
    """3. Modificar una API Key (Pausar/Activar, Renovar días o Cambiar nombre)."""
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM api_keys WHERE key = ?", (payload.api_key,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="API Key no encontrada")

        new_active = row["is_active"] if payload.is_active is None else (1 if payload.is_active else 0)
        new_name = row["client_name"] if payload.client_name is None else payload.client_name
        current_expiry = datetime.fromisoformat(row["expires_at"])
        
        # Si se solicita extensión de días:
        if payload.additional_days:
            base_time = max(datetime.now(timezone.utc), current_expiry)
            new_expiry = (base_time + timedelta(days=payload.additional_days)).isoformat()
        else:
            new_expiry = row["expires_at"]

        cursor.execute(
            "UPDATE api_keys SET is_active = ?, client_name = ?, expires_at = ? WHERE key = ?",
            (new_active, new_name, new_expiry, payload.api_key)
        )
        conn.commit()

    return {
        "message": "Key actualizada correctamente",
        "api_key": payload.api_key,
        "client_name": new_name,
        "is_active": bool(new_active),
        "expires_at": new_expiry
    }

@app.delete("/admin/keys/{api_key}", dependencies=[Depends(verify_admin_key)])
def delete_key(api_key: str):
    """4. Eliminar permanentemente una API Key."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM api_keys WHERE key = ?", (api_key,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="API Key no encontrada")
        conn.commit()
    return {"message": "API Key eliminada correctamente"}