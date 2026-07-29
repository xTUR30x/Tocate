import logging
import re
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from bs4 import BeautifulSoup
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tasas_bot")

latest_rates = {
    "usd": None,
    "eur": None,
    "date": None,
    "time": None,
    "updated_at": None,
    "status": "pending"
}


async def fetch_rates():
    global latest_rates

    logger.info("Iniciando scraping con Playwright...")

    try:

        async with async_playwright() as p:

            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )

            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/139.0.0.0 Safari/537.36"
                ),
                locale="es-ES",
                viewport={
                    "width": 1366,
                    "height": 768,
                },
            )

            page = await context.new_page()

            await page.goto(
                "https://eltoque.com/",
                wait_until="networkidle",
                timeout=60000,
            )

            # Espera unos segundos por si Cloudflare está validando
            await page.wait_for_timeout(5000)

            logger.info(f"Título: {await page.title()}")
            logger.info(f"URL final: {page.url}")

            html = await page.content()

            await browser.close()

        soup = BeautifulSoup(html, "html.parser")

        usd_val = None
        eur_val = None

        # -----------------------------
        # Estrategia 1
        # -----------------------------
        price_spans = soup.find_all(
            "span",
            class_=lambda c: c and "font-extrabold" in c and "text-lg" in c,
        )

        for span in price_spans:

            parent = span.find_parent(
                lambda tag: tag.name in ["div", "article", "tr", "li"]
                and (
                    "USD" in tag.get_text().upper()
                    or "EUR" in tag.get_text().upper()
                )
            )

            raw = span.get_text().replace("\xa0", " ").strip()

            m = re.search(r"[\d\.,]+", raw)

            if not m:
                continue

            value = m.group(0)

            if parent:

                txt = parent.get_text().upper()

                if "USD" in txt and usd_val is None:
                    usd_val = value

                elif "EUR" in txt and eur_val is None:
                    eur_val = value

        # -----------------------------
        # Estrategia 2 (Backup)
        # -----------------------------
        if usd_val is None or eur_val is None:

            full_text = soup.get_text()

            if usd_val is None:
                m = re.search(
                    r"USD[^\d]*([\d\.,]+)\s*CUP",
                    full_text,
                    re.IGNORECASE,
                )

                if m:
                    usd_val = m.group(1)

            if eur_val is None:
                m = re.search(
                    r"EUR[^\d]*([\d\.,]+)\s*CUP",
                    full_text,
                    re.IGNORECASE,
                )

                if m:
                    eur_val = m.group(1)

        now = datetime.now(timezone.utc)

        latest_rates.update(
            {
                "usd": usd_val,
                "eur": eur_val,
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "updated_at": now.isoformat(),
                "status": "success",
            }
        )

        logger.info(f"✅ USD={usd_val}")
        logger.info(f"✅ EUR={eur_val}")

    except Exception as e:

        logger.exception("Error durante el scraping")

        latest_rates["status"] = f"error: {e}"


scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):

    await fetch_rates()

    scheduler.add_job(
        fetch_rates,
        "interval",
        hours=1,
    )

    scheduler.start()

    yield

    scheduler.shutdown()


app = FastAPI(
    title="API Tasas El Toque",
    lifespan=lifespan,
)


@app.get("/")
def root():
    return {
        "message": "Servidor activo",
        "endpoints": {
            "rates": "/api/v1/rates",
            "refresh": "/api/v1/rates/refresh",
        },
    }


@app.get("/api/v1/rates")
def get_rates():
    return latest_rates


@app.post("/api/v1/rates/refresh")
async def refresh():
    await fetch_rates()

    return {
        "message": "Actualizado",
        "data": latest_rates,
    }