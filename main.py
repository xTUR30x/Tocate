from typing import Optional
from bs4 import BeautifulSoup
import httpx, re
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

app = FastAPI(
    title="Rates API",
    version="1.0.0",
    description="Servicio para extraer tasas informales de cambio desde Telegram",
)


class RateResponse(BaseModel):
    status: str = Field(
        default="success",
        description="Estado del resultado de la solicitud",
    )
    usd: Optional[float] = Field(
        default=None, description="Tasa del Dólar estadounidense (USD)"
    )
    eur: Optional[float] = Field(
        default=None, description="Tasa del Euro (EUR)"
    )
    published_at: Optional[str] = Field(
        default=None,
        description="Fecha y hora ISO 8601 de publicación del mensaje",
    )


def fetch_latest_rates_from_telegram(
    channel_username: str = "eltoquecom",
) -> RateResponse:
    url = f"https://t.me/s/{channel_username}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        response = httpx.get(url, headers=headers, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError as err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error al conectar con la vista web de Telegram: {str(err)}",
        )

    soup = BeautifulSoup(response.text, "html.parser")
    messages = soup.find_all("div", class_="tgme_widget_message")

    if not messages:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontraron mensajes en el canal especificado.",
        )

    # Recorremos los mensajes desde el más reciente hacia atrás
    for msg in reversed(messages):
        text_node = msg.find("div", class_="tgme_widget_message_text")
        if not text_node:
            continue

        raw_text = text_node.get_text(separator="\n").strip()

        # Regex para capturar importes numéricos tras las siglas USD/EUR
        usd_match = re.search(
            r"(?:USD|Dólar|Dolar)\s*[:=-]?\s*(\d+(?:\.\d+)?)",
            raw_text,
            re.IGNORECASE,
        )
        eur_match = re.search(
            r"(?:EUR|Euro)\s*[:=-]?\s*(\d+(?:\.\d+)?)",
            raw_text,
            re.IGNORECASE,
        )

        if usd_match or eur_match:
            time_node = msg.find("time", class_="time")
            published_at = time_node.get("datetime") if time_node else None

            return RateResponse(
                usd=float(usd_match.group(1)) if usd_match else None,
                eur=float(eur_match.group(1)) if eur_match else None,
                published_at=published_at,
                status="success",
            )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No se encontró ningún mensaje reciente que contenga tasas de cambio.",
    )


@app.get(
    "/api/v1/rates",
    response_model=RateResponse,
    status_code=status.HTTP_200_OK,
    summary="Obtener las últimas tasas de cambio",
)
async def get_rates():
    """Busca en el canal de Telegram el último mensaje publicado con los valores

    del USD y EUR, extrayendo los datos numéricos y la fecha/hora de
    publicación.
    """
    return fetch_latest_rates_from_telegram(channel_username="eltoquecom")